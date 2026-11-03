"""Review helper for public tag suggestions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from admin import ingest
from meme_pipeline.catalog import load_catalog
from meme_pipeline.config import Config
from meme_pipeline.maintenance import clean_tag
from meme_pipeline.util import ValidationError, atomic_write_json, iso_local


def _dir(cfg: Config) -> Path:
    path = cfg.root / "data" / "tag-suggestions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load(cfg: Config, suggestion_id: str) -> dict[str, Any]:
    path = _dir(cfg) / f"{suggestion_id}.json"
    if not path.is_file():
        raise ValidationError(f"tag suggestion not found: {suggestion_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError("invalid tag suggestion")
    return data


def list_suggestions(cfg: Config, status: str = "") -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted(_dir(cfg).glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if status and data.get("status") != status:
            continue
        data["preview_url"] = f"/api/admin/media/{data.get('image_id')}"
        result.append(data)
    return result


def approve(cfg: Config, suggestion_id: str, tags: list[str] | None = None) -> dict[str, Any]:
    record = _load(cfg, suggestion_id)
    image_id = str(record.get("image_id", ""))
    entries = load_catalog(cfg)
    entry = next((item for item in entries if str(item.get("id")) == image_id), None)
    if entry is None:
        raise ValidationError(f"image not found: {image_id}")
    existing = [str(value) for value in entry.get("tag", [])]
    proposed = tags if tags is not None else [str(value) for value in record.get("tags", [])]
    merged = list(existing)
    for value in proposed:
        tag = clean_tag(str(value))
        if tag not in merged:
            merged.append(tag)
    ingest.edit_image(image_id, tags=merged, root=cfg.root)
    record.update(
        {
            "status": "approved",
            "approved_tags": [str(value) for value in proposed],
            "reviewed_at": iso_local(),
            "review_note": "",
        }
    )
    atomic_write_json(_dir(cfg) / f"{suggestion_id}.json", record)
    return record


def reject(cfg: Config, suggestion_id: str, reason: str = "") -> dict[str, Any]:
    record = _load(cfg, suggestion_id)
    record.update({"status": "rejected", "reviewed_at": iso_local(), "review_note": reason[:500]})
    atomic_write_json(_dir(cfg) / f"{suggestion_id}.json", record)
    return record
