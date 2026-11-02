from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from .util import ValidationError

logger = logging.getLogger("meme_pipeline.imaging")

try:  # Pillow is recommended, not mandatory.
    from PIL import Image, UnidentifiedImageError

    HAS_PILLOW = True
    Image.MAX_IMAGE_PIXELS = 200_000_000
except ImportError:  # pragma: no cover - exercised only in minimal environments
    Image = None  # type: ignore[assignment]
    UnidentifiedImageError = Exception  # type: ignore[assignment,misc]
    HAS_PILLOW = False

FORMAT_EXT: dict[str, str] = {
    "jpeg": "jpg",
    "png": "png",
    "gif": "gif",
    "webp": "webp",
    "avif": "avif",
    "heif": "heif",
    "bmp": "bmp",
    "tiff": "tiff",
    "svg": "svg",
    "ico": "ico",
}

PIL_FORMAT_ALIASES = {
    "jpeg": "jpeg",
    "mpo": "jpeg",
    "png": "png",
    "gif": "gif",
    "webp": "webp",
    "avif": "avif",
    "heif": "heif",
    "bmp": "bmp",
    "tiff": "tiff",
    "ico": "ico",
}

# Formats that may not be decodable by the installed Pillow build.  The magic
# bytes are still authoritative and the original bytes are preserved.
SOFT_DECODE_FORMATS = {"avif", "heif", "svg"}


def detect_format(head: bytes) -> str | None:
    """Identify an image from its signature, never from the URL or MIME type."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "avif"
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return "heif"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith(b"II*\x00") or head.startswith(b"MM\x00*"):
        return "tiff"
    if head.startswith(b"\x00\x00\x01\x00"):
        return "ico"
    lowered = head[:512].lstrip().lower()
    if lowered.startswith(b"<?xml") or lowered.startswith(b"<svg"):
        if b"<svg" in head[:2048].lower():
            return "svg"
    return None


def extension_for(image_format: str) -> str:
    try:
        return FORMAT_EXT[image_format]
    except KeyError as exc:
        raise ValidationError(f"unsupported image format: {image_format}") from exc


@dataclass(slots=True)
class ImageInfo:
    format: str
    ext: str
    width: int | None
    height: int | None
    frames: int
    pillow_format: str | None
    dhash: str | None
    warnings: list[str] = field(default_factory=list)


def _probe_dimensions(head: bytes, image_format: str) -> tuple[int | None, int | None]:
    """Dependency-free dimension probe for common formats (fallback only)."""
    try:
        if image_format == "png" and len(head) >= 24:
            return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        if image_format == "gif" and len(head) >= 10:
            return int.from_bytes(head[6:8], "little"), int.from_bytes(head[8:10], "little")
        if image_format == "bmp" and len(head) >= 26:
            return (
                int.from_bytes(head[18:22], "little", signed=True),
                abs(int.from_bytes(head[22:26], "little", signed=True)),
            )
        if image_format == "webp" and len(head) >= 30 and head[12:16] == b"VP8X":
            width = 1 + int.from_bytes(head[24:27], "little")
            height = 1 + int.from_bytes(head[27:30], "little")
            return width, height
        if image_format == "jpeg":
            index = 2
            while index + 9 < len(head):
                if head[index] != 0xFF:
                    index += 1
                    continue
                marker = head[index + 1]
                if marker in {0xD8, 0xD9}:
                    index += 2
                    continue
                length = int.from_bytes(head[index + 2 : index + 4], "big")
                if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
                    height = int.from_bytes(head[index + 5 : index + 7], "big")
                    width = int.from_bytes(head[index + 7 : index + 9], "big")
                    return width, height
                index += 2 + max(length, 2)
    except (IndexError, ValueError):
        return None, None
    return None, None


def _pillow_format(image_format: str, pillow_format: str | None) -> str | None:
    if not pillow_format:
        return None
    normalized = PIL_FORMAT_ALIASES.get(pillow_format.lower(), pillow_format.lower())
    if normalized != image_format:
        raise ValidationError(
            f"file signature says {image_format}, Pillow decoded it as {pillow_format}"
        )
    return normalized


def compute_dhash(path: Path) -> str | None:
    if not HAS_PILLOW:
        return None
    try:
        with Image.open(path) as image:  # type: ignore[union-attr]
            image.seek(0)
            if image.mode in ("RGBA", "LA") or "transparency" in image.info:
                rgba = image.convert("RGBA")
                background = Image.new("RGB", rgba.size, (255, 255, 255))
                background.paste(rgba, mask=rgba.getchannel("A"))
                rgb = background
            else:
                rgb = image.convert("RGB")
            small = rgb.resize((9, 8), Image.Resampling.LANCZOS)
            pixels = small.load()
            bits = 0
            for y in range(8):
                for x in range(8):
                    left = pixels[x, y]
                    right = pixels[x + 1, y]
                    left_lum = 299 * left[0] + 587 * left[1] + 114 * left[2]
                    right_lum = 299 * right[0] + 587 * right[1] + 114 * right[2]
                    bits = (bits << 1) | (1 if left_lum > right_lum else 0)
            return f"{bits:016x}"
    except Exception as exc:  # noqa: BLE001 - a missing hash must not break a crawl
        logger.debug("dhash failed for %s: %s", path, exc)
        return None


def inspect_image(path: Path, *, expected_format: str | None = None, with_dhash: bool = True) -> ImageInfo:
    """Validate an image file and return its authoritative format/dimensions."""
    with path.open("rb") as handle:
        head = handle.read(4096)
    if not head:
        raise ValidationError(f"{path}: empty file")
    image_format = detect_format(head)
    if image_format is None:
        raise ValidationError(f"{path}: unrecognised image signature")
    if expected_format and image_format != expected_format:
        raise ValidationError(
            f"{path}: signature is {image_format}, expected {expected_format}"
        )
    ext = extension_for(image_format)
    warnings_list: list[str] = []
    width: int | None = None
    height: int | None = None
    frames = 1
    pillow_format: str | None = None

    if HAS_PILLOW and image_format != "svg":
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with Image.open(path) as image:  # type: ignore[union-attr]
                    pillow_format = _pillow_format(image_format, image.format)
                    width, height = image.size
                    frames = int(getattr(image, "n_frames", 1) or 1)
                    image.load()
                for item in caught:
                    warnings_list.append(str(item.message))
        except ValidationError:
            raise
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            if image_format not in SOFT_DECODE_FORMATS:
                raise ValidationError(f"{path}: Pillow could not decode {image_format}: {exc}") from exc
            warnings_list.append(f"Pillow could not decode {image_format}: {exc}")
    else:
        width, height = _probe_dimensions(head, image_format)
        if not HAS_PILLOW:
            warnings_list.append("Pillow is not installed; dimensions/perceptual hash are best-effort")
        elif image_format == "svg":
            warnings_list.append("SVG is preserved but not raster-decoded")

    return ImageInfo(
        format=image_format,
        ext=ext,
        width=width,
        height=height,
        frames=frames,
        pillow_format=pillow_format,
        dhash=compute_dhash(path) if with_dhash else None,
        warnings=warnings_list,
    )


def hamming_distance_hex(left: str, right: str) -> int:
    return (int(left, 16) ^ int(right, 16)).bit_count()


def find_near_duplicate_pairs(
    items: list[tuple[str, str]],
    *,
    threshold: int,
    limit: int,
) -> tuple[list[dict[str, object]], bool]:
    """Return near-duplicate pairs (by 64-bit dHash), capped for sane reports."""
    pairs: list[dict[str, object]] = []
    truncated = False
    for i in range(len(items)):
        left_id, left_hash = items[i]
        for j in range(i + 1, len(items)):
            right_id, right_hash = items[j]
            distance = hamming_distance_hex(left_hash, right_hash)
            if distance <= threshold:
                if len(pairs) >= limit:
                    truncated = True
                    continue
                pairs.append(
                    {
                        "a": left_id,
                        "b": right_id,
                        "distance": distance,
                        "threshold": threshold,
                    }
                )
    return pairs, truncated
