#!/usr/bin/env python3
"""Build WebP card thumbnails from img/converted, without touching originals.

    .venv/bin/python tools/build_thumbnails.py
    .venv/bin/python tools/build_thumbnails.py --force
    .venv/bin/python tools/build_thumbnails.py --sizes 320,640 --workers 4

Output:
    img/thumbs/320/<id>.webp
    img/thumbs/640/<id>.webp
    img/thumbs/manifest.json

The manifest records the source image, original dimensions and the generated
thumbnail dimensions/bytes. Animated GIFs use the first frame for the card
thumbnail; the original GIF is still served by /api/images/{id}.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIZES = (320, 640)
DEFAULT_QUALITY = {320: 80, 640: 84}


def _source_path(converted_dir: Path, image_id: str) -> Path | None:
    for suffix in (".gif", ".jpg"):
        path = converted_dir / f"{image_id}{suffix}"
        if path.is_file():
            return path
    return None


def _build_one(
    entry: dict[str, Any],
    converted_dir: Path,
    output_dir: Path,
    sizes: tuple[int, ...],
    force: bool,
) -> dict[str, Any]:
    image_id = str(entry["id"])
    source = _source_path(converted_dir, image_id)
    if source is None:
        raise FileNotFoundError(f"converted image missing for id={image_id}")

    result: dict[str, Any] = {
        "source": source.relative_to(ROOT).as_posix(),
        "source_format": entry.get("format"),
        "width": entry.get("width"),
        "height": entry.get("height"),
        "thumbs": {},
    }

    with Image.open(source) as opened:
        if getattr(opened, "is_animated", False):
            opened.seek(0)
        has_alpha = opened.mode in ("RGBA", "LA", "P") or "transparency" in opened.info
        image = opened.convert("RGBA" if has_alpha else "RGB")
        image = ImageOps.exif_transpose(image)
        original_size = image.size

        for size in sizes:
            target = output_dir / str(size) / f"{image_id}.webp"
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.is_file() and not force:
                with Image.open(target) as existing:
                    tw, th = existing.size
                result["thumbs"][str(size)] = {
                    "path": target.relative_to(ROOT).as_posix(),
                    "width": tw,
                    "height": th,
                    "bytes": target.stat().st_size,
                }
                continue

            thumb = image.copy()
            thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
            # Never upscale tiny originals; keep the actual output dimensions.
            tw, th = thumb.size
            save_kwargs: dict[str, Any] = {
                "format": "WEBP",
                "quality": DEFAULT_QUALITY.get(size, 82),
                "method": 6,
            }
            if has_alpha:
                save_kwargs["lossless"] = False
            thumb.save(target, **save_kwargs)
            result["thumbs"][str(size)] = {
                "path": target.relative_to(ROOT).as_posix(),
                "width": tw,
                "height": th,
                "bytes": target.stat().st_size,
            }

    result["original_width"], result["original_height"] = original_size
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Build local WebP thumbnails for the meme library")
    parser.add_argument("--data-dir", type=Path, default=ROOT)
    parser.add_argument("--sizes", default="320,640")
    parser.add_argument("--workers", type=int, default=min(8, (os.cpu_count() or 2)))
    parser.add_argument("--force", action="store_true", help="overwrite existing thumbnails")
    args = parser.parse_args()

    data_dir = args.data_dir.resolve()
    catalog_path = data_dir / "data" / "catalog.json"
    converted_dir = data_dir / "img" / "converted"
    output_dir = data_dir / "img" / "thumbs"
    if not catalog_path.is_file():
        raise SystemExit(f"catalog not found: {catalog_path}")
    if not converted_dir.is_dir():
        raise SystemExit(f"converted dir not found: {converted_dir}")

    try:
        sizes = tuple(sorted({int(value.strip()) for value in args.sizes.split(",") if value.strip()}))
    except ValueError:
        raise SystemExit("--sizes must be comma-separated integers, e.g. 320,640")
    if not sizes or any(size < 32 or size > 2048 for size in sizes):
        raise SystemExit("sizes must be between 32 and 2048")

    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(catalog, list):
        raise SystemExit("catalog must be an array")

    output_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}
    failures: list[dict[str, str]] = []
    updated = 0
    total = len(catalog)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")

    def work(entry: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        return str(entry["id"]), _build_one(entry, converted_dir, output_dir, sizes, args.force)

    with ThreadPoolExecutor(max_workers=max(1, min(16, args.workers))) as pool:
        futures = {pool.submit(work, entry): entry for entry in catalog}
        for index, future in enumerate(as_completed(futures), 1):
            entry = futures[future]
            image_id = str(entry.get("id", "?"))
            try:
                _, payload = future.result()
                results[image_id] = payload
                updated += 1
            except Exception as exc:  # noqa: BLE001 - retain a report for every failure
                failures.append({"id": image_id, "error": f"{type(exc).__name__}: {exc}"})
            if index % 100 == 0 or index == total:
                print(f"  {index}/{total}", flush=True)

    results = dict(sorted(results.items(), key=lambda pair: int(pair[0])))
    manifest = {
        "schema_version": 1,
        "generated_at": started_at,
        "source": "img/converted",
        "sizes": list(sizes),
        "count": len(results),
        "failures": failures,
        "entries": results,
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    thumb_bytes = sum(
        int(item.get("bytes", 0))
        for payload in results.values()
        for item in payload.get("thumbs", {}).values()
    )
    print(f"\nthumbnails: {len(results)}/{total} images, {thumb_bytes / 1024 / 1024:.1f} MiB")
    print(f"manifest:   {manifest_path.relative_to(data_dir)}")
    if failures:
        print(f"failures:   {len(failures)} (see manifest)")
        for failure in failures[:10]:
            print(f"  {failure['id']}: {failure['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
