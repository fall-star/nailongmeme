#!/usr/bin/env python3
"""Convert a raw meme folder into a JPG/GIF delivery folder.

Rules
-----
* Animated images  -> .gif
* Static images    -> .jpg
* Animated GIFs are copied **byte-for-byte** so frame timing, loop count,
  transparency and disposal are exactly the same as the source.
* Animated WebP/APNG are re-encoded to GIF with the source frame count,
  per-frame durations and loop count.  GIF cannot represent alpha gradients,
  so alpha is composited onto the configured background (white by default).
* Static JPEGs are copied losslessly by default (use --reencode-jpeg to force
  a re-encode).  Other static formats are decoded and saved as JPEG at the
  requested quality; alpha is composited onto the configured background.

Usage
-----
    python3 convert_formats.py --src img/raw --out img/converted
    ./run.sh convert --src img/raw --out img/converted

The output folder also receives conversion-manifest.json with per-file
source/output hashes, dimensions, frame metadata and any warnings.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageColor, ImageOps
except ImportError:  # pragma: no cover - depends on the interpreter
    print(
        "error: Pillow is required for conversion.\n"
        "  install: python3 -m pip install -r requirements.txt\n"
        "  or use:  ./run.sh convert ...  (auto-detects a Python with Pillow)",
        file=sys.stderr,
    )
    raise SystemExit(1)

Image.MAX_IMAGE_PIXELS = 200_000_000

IMAGE_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".jpe",
    ".jfif",
    ".png",
    ".gif",
    ".webp",
    ".avif",
    ".bmp",
    ".tif",
    ".tiff",
}
JPEG_FORMATS = {"JPEG", "MPO"}
GIF_DURATION_TOLERANCE_MS = 10


class ConversionError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Helpers


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _temp_path(destination: Path) -> Path:
    fd, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(fd)
    return Path(name)


def _atomic_copy(source: Path, destination: Path) -> None:
    tmp = _temp_path(destination)
    try:
        shutil.copyfile(source, tmp)
        os.chmod(tmp, 0o644)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _looks_numeric(value: str) -> bool:
    return value.isdigit()


def _duration_of(image: Image.Image) -> int | None:
    value = image.info.get("duration")
    if value is None:
        value = getattr(image, "duration", None)
    if value is None:
        delay = image.info.get("delay")
        if delay is not None:
            try:
                value = float(delay) * 10.0
            except (TypeError, ValueError):
                value = None
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _webp_animation_meta(path: Path) -> tuple[list[int], int] | None:
    """Parse WebP ANIM/ANMF chunks for durations and loop count.

    Pillow does not expose per-frame WebP durations on every version, while the
    container itself stores them losslessly.  This fallback keeps animated WebP
    playback as close as GIF allows.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    offset = 12
    loop = 0
    durations: list[int] = []
    while offset + 8 <= len(data):
        fourcc = data[offset : offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        payload = offset + 8
        end = payload + size
        if end > len(data):
            break
        if fourcc == b"ANIM" and payload + 6 <= len(data):
            loop = struct.unpack_from("<H", data, payload + 4)[0]
        elif fourcc == b"ANMF" and payload + 15 <= len(data):
            durations.append(int.from_bytes(data[payload + 12 : payload + 15], "little"))
        offset = end + (size & 1)
    if not durations:
        return None
    return durations, loop


@dataclass(slots=True)
class SourceMeta:
    format: str
    animated: bool
    frames: int
    durations: list[int]
    loop: int
    width: int
    height: int


def inspect_source(path: Path) -> SourceMeta:
    try:
        with Image.open(path) as image:
            image_format = (image.format or "").upper()
            if not image_format:
                raise ConversionError("Pillow could not determine the image format")
            frames = int(getattr(image, "n_frames", 1) or 1)
            animated = frames > 1 or bool(getattr(image, "is_animated", False))
            raw_durations: list[int | None] = []
            if animated:
                for index in range(frames):
                    image.seek(index)
                    raw_durations.append(_duration_of(image))
                loop = int(image.info.get("loop", 0) or 0)
                image.seek(0)
                if image_format == "WEBP" and any(value is None for value in raw_durations):
                    parsed = _webp_animation_meta(path)
                    if parsed is not None and len(parsed[0]) == frames:
                        parsed_durations, parsed_loop = parsed
                        raw_durations = list(parsed_durations)
                        loop = parsed_loop
            else:
                loop = 0
            durations = [100 if value is None else value for value in raw_durations]
            width, height = image.size
            return SourceMeta(
                format=image_format,
                animated=animated,
                frames=frames,
                durations=durations,
                loop=loop,
                width=width,
                height=height,
            )
    except ConversionError:
        raise
    except Exception as exc:  # noqa: BLE001 - reported per file
        raise ConversionError(f"{type(exc).__name__}: {exc}") from exc


@dataclass(slots=True)
class ConversionResult:
    source: str
    source_id: str
    source_format: str = ""
    animated: bool = False
    mode: str = ""
    status: str = "pending"  # converted / skipped / failed
    output: str = ""
    source_sha256: str = ""
    output_sha256: str = ""
    source_bytes: int = 0
    output_bytes: int = 0
    width: int | None = None
    height: int | None = None
    source_frames: int = 0
    output_frames: int = 0
    source_durations: list[int] = field(default_factory=list)
    output_durations: list[int] = field(default_factory=list)
    loop: int = 0
    sha_equal: bool | None = None
    playback_preserved: bool | None = None
    transparency_flattened: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Conversion


def _composite_on_background(image: Image.Image, background: tuple[int, int, int]) -> Image.Image:
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, background)
        canvas.paste(rgba, mask=rgba.getchannel("A"))
        return canvas
    return image.convert("RGB")


def _convert_static(
    source: Path,
    destination: Path,
    meta: SourceMeta,
    *,
    jpeg_quality: int,
    background: tuple[int, int, int],
    copy_jpeg: bool,
) -> str:
    if meta.format in JPEG_FORMATS and copy_jpeg:
        _atomic_copy(source, destination)
        return "copy_jpeg"

    tmp = _temp_path(destination)
    try:
        with Image.open(source) as image:
            image.seek(0)
            image = ImageOps.exif_transpose(image)
            if image is None:  # pragma: no cover - Pillow always returns an image
                raise ConversionError("Pillow returned no image")
            rgb = _composite_on_background(image, background)
            rgb.save(
                tmp,
                format="JPEG",
                quality=jpeg_quality,
                subsampling=0,
                optimize=True,
            )
        os.chmod(tmp, 0o644)
        os.replace(tmp, destination)
    finally:
        tmp.unlink(missing_ok=True)
    return "to_jpg"


def _convert_animated(
    source: Path,
    destination: Path,
    meta: SourceMeta,
    *,
    background: tuple[int, int, int],
) -> tuple[str, bool]:
    """Return (mode, transparency_flattened).  GIF inputs are copied exactly."""
    if meta.format == "GIF":
        _atomic_copy(source, destination)
        return "copy_gif", False

    tmp = _temp_path(destination)
    frames: list[Image.Image] = []
    had_alpha = False
    try:
        with Image.open(source) as image:
            canvas_size = image.size
            for index in range(meta.frames):
                image.seek(index)
                frame = image.convert("RGBA")
                if frame.getchannel("A").getextrema()[0] < 255:
                    had_alpha = True
                if frame.size != canvas_size:
                    canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
                    canvas.alpha_composite(frame, (0, 0))
                    frame = canvas
                flattened = _composite_on_background(frame, background)
                frames.append(flattened.convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
            if not frames:
                raise ConversionError("animated image has no frames")
            durations = meta.durations or [100] * len(frames)
            if len(durations) != len(frames):
                durations = (durations + [100] * len(frames))[: len(frames)]
            frames[0].save(
                tmp,
                format="GIF",
                save_all=True,
                append_images=frames[1:],
                duration=durations,
                loop=meta.loop,
                disposal=2,
                optimize=False,
            )
        os.chmod(tmp, 0o644)
        os.replace(tmp, destination)
    finally:
        for frame in frames:
            frame.close()
        tmp.unlink(missing_ok=True)
    return "to_gif", had_alpha


def _gif_metadata(path: Path) -> tuple[int, list[int], int, tuple[int, int]]:
    with Image.open(path) as image:
        frames = int(getattr(image, "n_frames", 1) or 1)
        durations: list[int] = []
        for index in range(frames):
            image.seek(index)
            duration = _duration_of(image)
            durations.append(100 if duration is None else duration)
        loop = int(image.info.get("loop", 0) or 0)
        return frames, durations, loop, image.size


def _durations_close(left: list[int], right: list[int]) -> bool:
    if len(left) != len(right):
        return False
    return all(abs(a - b) <= GIF_DURATION_TOLERANCE_MS for a, b in zip(left, right))


def _verify(
    source: Path,
    destination: Path,
    meta: SourceMeta,
    result: ConversionResult,
) -> None:
    result.output_sha256 = _sha256_file(destination)
    result.output_bytes = destination.stat().st_size
    if result.mode == "copy_jpeg":
        result.sha_equal = result.output_sha256 == result.source_sha256
        with Image.open(destination) as image:
            if (image.format or "").upper() not in JPEG_FORMATS:
                raise ConversionError(f"output is not JPEG: {image.format}")
            image.load()
            result.width, result.height = image.size
        result.playback_preserved = None
        return

    if not meta.animated:
        with Image.open(destination) as image:
            if (image.format or "").upper() not in JPEG_FORMATS:
                raise ConversionError(f"output is not JPEG: {image.format}")
            image.load()
            result.output_frames = 1
            result.width, result.height = image.size
        if (result.width, result.height) != (meta.width, meta.height):
            raise ConversionError(
                f"static dimensions changed: {meta.width}x{meta.height} -> "
                f"{result.width}x{result.height}"
            )
        result.playback_preserved = None
        return

    out_frames, out_durations, out_loop, out_size = _gif_metadata(destination)
    result.output_frames = out_frames
    result.output_durations = out_durations
    result.width, result.height = out_size
    if out_frames != meta.frames:
        raise ConversionError(f"animated frame count changed: {meta.frames} -> {out_frames}")
    if (out_size[0], out_size[1]) != (meta.width, meta.height):
        raise ConversionError(
            f"animated dimensions changed: {meta.width}x{meta.height} -> {out_size[0]}x{out_size[1]}"
        )
    if result.mode == "copy_gif":
        result.sha_equal = result.output_sha256 == result.source_sha256
        result.playback_preserved = (
            result.sha_equal
            and out_loop == meta.loop
            and out_durations == meta.durations
        )
        if not result.playback_preserved:
            raise ConversionError("GIF copy did not preserve its SHA-256/frame metadata")
        return

    result.sha_equal = False
    result.playback_preserved = _durations_close(out_durations, meta.durations) and out_loop == meta.loop
    if not result.playback_preserved:
        raise ConversionError(
            "animated GIF playback differs: "
            f"durations {meta.durations} -> {out_durations}, loop {meta.loop} -> {out_loop}"
        )


def convert_one(
    source: Path,
    output_dir: Path,
    *,
    jpeg_quality: int,
    background: tuple[int, int, int],
    copy_jpeg: bool,
    force: bool,
) -> ConversionResult:
    result = ConversionResult(
        source=_display_path(source),
        source_id=source.stem,
        source_bytes=source.stat().st_size,
        source_sha256=_sha256_file(source),
    )
    try:
        meta = inspect_source(source)
        result.source_format = meta.format
        result.animated = meta.animated
        result.source_frames = meta.frames
        result.source_durations = list(meta.durations)
        result.loop = meta.loop
        extension = ".gif" if meta.animated else ".jpg"
        destination = output_dir / f"{source.stem}{extension}"
        stale = output_dir / f"{source.stem}{'.jpg' if extension == '.gif' else '.gif'}"
        result.output = _display_path(destination)
        if meta.animated:
            result.mode = "copy_gif" if meta.format == "GIF" else "to_gif"
        else:
            result.mode = "copy_jpeg" if meta.format in JPEG_FORMATS and copy_jpeg else "to_jpg"
        if stale.exists() and stale != destination:
            stale.unlink()
            result.warnings.append(f"removed stale file from a previous mode: {stale.name}")

        if destination.exists() and not force:
            result.status = "skipped"
            result.output_bytes = destination.stat().st_size
            result.output_sha256 = _sha256_file(destination)
            return result

        if meta.animated:
            mode, flattened = _convert_animated(
                source, destination, meta, background=background
            )
            result.mode = mode
            result.transparency_flattened = flattened
            if flattened:
                result.warnings.append(
                    "animated source had alpha; GIF transparency was flattened onto the background"
                )
        else:
            result.mode = _convert_static(
                source,
                destination,
                meta,
                jpeg_quality=jpeg_quality,
                background=background,
                copy_jpeg=copy_jpeg,
            )
        _verify(source, destination, meta, result)
        result.status = "converted"
    except Exception as exc:  # noqa: BLE001 - reported per file
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def _scan_sources(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        raise ConversionError(f"source directory does not exist: {source_dir}")
    paths = [
        path
        for path in source_dir.iterdir()
        if path.is_file() and not path.name.startswith(".") and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    if not paths:
        raise ConversionError(f"no supported images found in {source_dir}")
    seen: dict[str, Path] = {}
    for path in paths:
        if path.stem in seen:
            raise ConversionError(
                f"duplicate output stem {path.stem!r}: {seen[path.stem].name} and {path.name}"
            )
        seen[path.stem] = path
    paths.sort(key=lambda path: (0, int(path.stem)) if _looks_numeric(path.stem) else (1, path.stem))
    return paths


def run_conversion(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    jpeg_quality: int = 95,
    background: str = "#FFFFFF",
    copy_jpeg: bool = True,
    force: bool = False,
    workers: int = 8,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    if not 1 <= jpeg_quality <= 100:
        raise ConversionError("--jpeg-quality must be between 1 and 100")
    if not 1 <= workers <= 32:
        raise ConversionError("--workers must be between 1 and 32")
    try:
        background_rgb = ImageColor.getrgb(background)
    except ValueError as exc:
        raise ConversionError(f"invalid background colour: {background!r}") from exc
    if len(background_rgb) == 4:  # ImageColor can return RGBA for named colours
        background_rgb = background_rgb[:3]

    source_root = Path(source_dir).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    if source_root == output_root:
        raise ConversionError("source and output directories must differ")
    if output_root.is_relative_to(source_root):
        raise ConversionError("output directory must be outside the source directory")
    paths = _scan_sources(source_root)
    if limit is not None:
        if limit <= 0:
            raise ConversionError("--limit must be a positive integer")
        paths = paths[:limit]

    settings = {
        "jpeg_quality": jpeg_quality,
        "background": background,
        "copy_jpeg": copy_jpeg,
        "copy_animated_gif": True,
        "animated_non_gif": "re-encode to GIF, preserve frame count/durations/loop, flatten alpha",
    }

    if dry_run:
        plans = []
        for path in paths:
            meta = inspect_source(path)
            plans.append(
                {
                    "source": _display_path(path),
                    "target": _display_path(output_root / (path.stem + (".gif" if meta.animated else ".jpg"))),
                    "format": meta.format,
                    "animated": meta.animated,
                    "frames": meta.frames,
                }
            )
        static_count = sum(1 for plan in plans if not plan["animated"])
        print(f"DRY RUN — {len(plans)} source image(s)")
        print(f"  static -> JPG:   {static_count}")
        print(f"  animated -> GIF: {len(plans) - static_count}")
        for plan in plans[:5]:
            print(f"  {plan['source']} -> {plan['target']}")
        if len(plans) > 5:
            print(f"  ... {len(plans) - 5} more")
        return {
            "dry_run": True,
            "source_dir": _display_path(source_root),
            "output_dir": _display_path(output_root),
            "settings": settings,
            "total": len(plans),
            "static": static_count,
            "animated": len(plans) - static_count,
            "plans": plans,
        }

    output_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    results: list[ConversionResult] = []
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="meme-convert"
    ) as executor:
        futures = {
            executor.submit(
                convert_one,
                path,
                output_root,
                jpeg_quality=jpeg_quality,
                background=background_rgb,
                copy_jpeg=copy_jpeg,
                force=force,
            ): path
            for path in paths
        }
        completed = 0
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            source = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 - should normally be caught in convert_one
                results.append(
                    ConversionResult(
                        source=_display_path(source),
                        source_id=source.stem,
                        status="failed",
                        error=f"worker crashed: {type(exc).__name__}: {exc}",
                    )
                )
            if completed % 50 == 0 or completed == len(paths):
                print(f"converted {completed}/{len(paths)} ...", flush=True)

    results.sort(key=lambda item: (0, int(item.source_id)) if _looks_numeric(item.source_id) else (1, item.source_id))
    counts = Counter(result.status for result in results)
    modes = Counter(result.mode for result in results if result.status in {"converted", "skipped"})
    failures = [result for result in results if result.status == "failed"]
    elapsed = time.monotonic() - started
    report = {
        "schema_version": 1,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source_dir": _display_path(source_root),
        "output_dir": _display_path(output_root),
        "settings": settings,
        "counts": {
            "total": len(results),
            "converted": counts.get("converted", 0),
            "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0),
            "static_jpg": sum(
                1 for result in results if result.status in {"converted", "skipped"} and not result.animated
            ),
            "animated_gif": sum(
                1 for result in results if result.status in {"converted", "skipped"} and result.animated
            ),
            "copied_gif": modes.get("copy_gif", 0),
            "copied_jpeg": modes.get("copy_jpeg", 0),
            "reencoded": modes.get("to_jpg", 0) + modes.get("to_gif", 0),
        },
        "elapsed_seconds": round(elapsed, 3),
        "items": [asdict(result) for result in results],
        "failures": [
            {"source": result.source, "error": result.error} for result in failures
        ],
    }
    report_path = output_root / "conversion-manifest.json"
    _atomic_write_json(report_path, report)

    print("Conversion complete")
    print(f"  source:        {_display_path(source_root)}")
    print(f"  output:        {_display_path(output_root)}")
    print(f"  total:         {len(results)}")
    print(f"  converted:     {counts.get('converted', 0)}")
    print(f"  skipped:       {counts.get('skipped', 0)}")
    print(f"  failed:        {counts.get('failed', 0)}")
    print(f"  static JPG:    {report['counts']['static_jpg']}")
    print(f"  animated GIF:  {report['counts']['animated_gif']} (copied: {report['counts']['copied_gif']})")
    print(f"  copied JPEG:   {report['counts']['copied_jpeg']}")
    print(f"  re-encoded:    {report['counts']['reencoded']}")
    print(f"  elapsed:       {elapsed:.1f}s")
    print(f"  manifest:      {_display_path(report_path)}")
    if failures:
        for failure in failures[:10]:
            print(f"  FAILED {failure.source}: {failure.error}", file=sys.stderr)
        if len(failures) > 10:
            print(f"  ... {len(failures) - 10} more failures", file=sys.stderr)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="convert_formats.py",
        description="Convert static images to JPG and animated images to GIF.",
    )
    parser.add_argument("--src", default="img/raw", help="source folder (default: img/raw)")
    parser.add_argument("--out", default="img/converted", help="output folder (default: img/converted)")
    parser.add_argument("--jpeg-quality", type=int, default=95, help="JPEG quality, 1-100 (default: 95)")
    parser.add_argument(
        "--background",
        default="#FFFFFF",
        help="background colour for transparent static images / animated alpha (default: #FFFFFF)",
    )
    parser.add_argument(
        "--reencode-jpeg",
        action="store_true",
        help="re-encode static JPEGs instead of copying them losslessly",
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing outputs")
    parser.add_argument("--workers", type=int, default=8, help="parallel workers (default: 8)")
    parser.add_argument("--limit", type=int, default=None, help="only process the first N images")
    parser.add_argument("--dry-run", action="store_true", help="classify and print only; write nothing")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_conversion(
            args.src,
            args.out,
            jpeg_quality=args.jpeg_quality,
            background=args.background,
            copy_jpeg=not args.reencode_jpeg,
            force=args.force,
            workers=args.workers,
            limit=args.limit,
            dry_run=args.dry_run,
        )
    except (ConversionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if report.get("dry_run"):
        return 0
    return 1 if report.get("failures") else 0


if __name__ == "__main__":
    raise SystemExit(main())
