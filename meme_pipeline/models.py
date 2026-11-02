from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SourceItem:
    """One meme as advertised by a source site."""

    source_item_id: str
    name: str
    image_url: str
    page_url: str = ""
    description: str = ""
    category: str = ""
    kind: str = ""
    source_label: str = ""
    source_page_url: str = ""
    published_at: dt.datetime | None = None
    source_order: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class FetchResult:
    items: list[SourceItem]
    snapshot_bytes: bytes | None = None
    snapshot_suffix: str = ".json"
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Downloaded:
    """A verified image sitting in the staging directory."""

    item: SourceItem
    index: int
    staging_path: Path
    sha256: str
    format: str
    ext: str
    width: int | None
    height: int | None
    frames: int
    byte_size: int
    content_type: str
    final_url: str
    dhash: str | None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DownloadFailure:
    index: int
    item: SourceItem
    error: str


@dataclass(slots=True)
class DuplicateRecord:
    key: str
    kept_index: int
    duplicate_index: int
    item: SourceItem
    existing_id: str | None = None
