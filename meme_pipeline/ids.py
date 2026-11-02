from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .util import ValidationError

_ID_RE = re.compile(r"^[0-9]+$")
_IMAGE_NAME_RE = re.compile(r"^([0-9]+)\.[a-z0-9]+$")


def parse_id(value: object, max_digits: int) -> int:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValidationError(f"id must be a decimal string, got {value!r}")
    if len(value) > max_digits:
        raise ValidationError(f"id {value!r} exceeds the configured {max_digits}-digit limit")
    number = int(value, 10)
    if number < 1:
        raise ValidationError(f"id must be >= 1, got {value!r}")
    return number


def format_id(number: int, min_width: int, max_digits: int) -> str:
    if number < 1:
        raise ValidationError(f"id must be >= 1, got {number}")
    text = str(number)
    if len(text) > max_digits:
        raise ValidationError(
            f"id {number} needs {len(text)} digits but the limit is {max_digits}; "
            "raise ids.max_digits in pipeline.json (and update the spec) before continuing"
        )
    if min_width:
        text = text.zfill(min_width)
    return text


def id_limit(max_digits: int) -> int:
    return 10**max_digits - 1


def ids_from_catalog(entries: Iterable[dict], max_digits: int) -> set[int]:
    numbers: set[int] = set()
    for entry in entries:
        number = parse_id(entry.get("id"), max_digits)
        if number in numbers:
            raise ValidationError(f"duplicate numeric id {number} in catalog")
        numbers.add(number)
    return numbers


def ids_from_image_dir(image_dir: Path, max_digits: int) -> set[int]:
    numbers: set[int] = set()
    if not image_dir.exists():
        return numbers
    for path in image_dir.iterdir():
        if not path.is_file() or path.name.startswith("."):
            continue
        match = _IMAGE_NAME_RE.fullmatch(path.name)
        if not match:
            continue
        raw = match.group(1)
        if len(raw) > max_digits:
            raise ValidationError(f"image filename id {raw!r} exceeds the {max_digits}-digit limit")
        numbers.add(int(raw, 10))
    return numbers


def allocate_ids(
    count: int,
    *,
    existing_numbers: Iterable[int],
    next_id: int,
    min_width: int,
    max_digits: int,
) -> tuple[list[str], int]:
    """Allocate ``count`` never-reused ids, skipping orphan files/catalog ids."""
    used = set(existing_numbers)
    limit = id_limit(max_digits)
    current = max(int(next_id), (max(used) + 1) if used else 1, 1)
    if current < 1:
        current = 1
    allocated: list[str] = []
    for _ in range(count):
        while current in used:
            current += 1
        if current > limit:
            raise ValidationError(
                f"id space exhausted: all {max_digits}-digit ids are used. "
                "Increase ids.max_digits in pipeline.json before crawling more."
            )
        allocated.append(format_id(current, min_width, max_digits))
        used.add(current)
        current += 1
    return allocated, current
