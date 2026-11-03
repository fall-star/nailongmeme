"""File scanning, conversion and catalog writes for the local admin app.

This module is deliberately not imported by the public API.  It is only used
by ``admin.server`` on 127.0.0.1 and writes to the local development catalog.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageOps

from convert_formats import convert_one
from meme_pipeline import SCHEMA_VERSION
from meme_pipeline.catalog import (
    compact_catalog_entry,
    load_catalog,
    load_manifest,
    load_state,
    save_catalog,
    save_manifest,
    save_state,
    validate_catalog,
)
from meme_pipeline.config import Config, load_config
from meme_pipeline.ids import allocate_ids
from meme_pipeline.imaging import extension_for, inspect_image
from meme_pipeline.maintenance import clean_tag
from meme_pipeline.util import (
    FileLock,
    ValidationError,
    atomic_write_json,
    clean_name,
    iso_local,
    new_run_id,
    sha256_file,
)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".ico"}
MAX_FILES = 500
MAX_BYTES = 50 * 1024 * 1024
THUMB_SIZES = (320, 640)


from meme_pipeline.ids import ids_from_catalog, ids_from_image_dir


@dataclass(slots=True)
class ScanItem:
    token: str
    source_path: Path
    relative_path: str
    display_name: str
    status: str = "ready"  # ready | duplicate | invalid
    reason: str = ""
    duplicate_id: str | None = None
    image_format: str | None = None
    extension: str | None = None
    width: int | None = None
    height: int | None = None
    frames: int = 1
    bytes: int = 0
    sha256: str = ""
    dhash: str | None = None
    warnings: list[str] = field(default_factory=list)

    def public(self, scan_id: str) -> dict[str, Any]:
        return {
            "token": self.token,
            "name": self.display_name,
            "relative_path": self.relative_path,
            "status": self.status,
            "reason": self.reason,
            "duplicate_id": self.duplicate_id,
            "format": self.image_format,
            "width": self.width,
            "height": self.height,
            "frames": self.frames,
            "bytes": self.bytes,
            "sha256": self.sha256[:12],
            "warnings": self.warnings,
            "preview_url": f"/api/admin/scans/{scan_id}/preview/{self.token}",
        }


@dataclass(slots=True)
class Scan:
    scan_id: str
    source_dir: Path | None
    items: list[ScanItem]
    created_at: str = field(default_factory=iso_local)


def get_config(root: Path | str | None = None) -> Config:
    cfg = load_config(root or os.getenv("MEME_DATA_DIR") or Path.cwd())
    cfg.ensure_dirs()
    return cfg


def _safe_display_name(path: Path) -> str:
    name = clean_name(path.stem) or path.stem
    return name[:120]


def _hash(path: Path) -> str:
    return sha256_file(path)


def _catalog_hashes(cfg: Config) -> dict[str, str]:
    by_sha: dict[str, str] = {}
    for entry in load_catalog(cfg):
        digest = entry.get("sha256")
        if isinstance(digest, str):
            by_sha[digest] = str(entry.get("id"))
    return by_sha


def scan_paths(paths: Iterable[Path], *, root: Path | None = None, catalog: list[dict[str, Any]] | None = None) -> Scan:
    cfg = get_config(root)
    entries = catalog if catalog is not None else load_catalog(cfg)
    by_sha = {
        str(entry.get("sha256")): str(entry.get("id"))
        for entry in entries
        if isinstance(entry.get("sha256"), str)
    }
    seen_hashes: dict[str, str] = {}
    items: list[ScanItem] = []
    for index, raw_path in enumerate(paths):
        path = raw_path.resolve()
        token = f"{index:04d}"
        rel = path.name
        item = ScanItem(token=token, source_path=path, relative_path=rel, display_name=_safe_display_name(path))
        try:
            if not path.is_file():
                raise ValidationError("not a regular file")
            if path.stat().st_size > MAX_BYTES:
                raise ValidationError(f"file is larger than {MAX_BYTES // 1024 // 1024} MB")
            meta = inspect_image(path)
            item.image_format = meta.format
            item.extension = extension_for(meta.format)
            item.width = meta.width
            item.height = meta.height
            item.frames = meta.frames
            item.bytes = path.stat().st_size
            item.dhash = meta.dhash
            item.warnings = list(meta.warnings)
            digest = _hash(path)
            item.sha256 = digest
            if digest in by_sha:
                item.status = "duplicate"
                item.duplicate_id = by_sha[digest]
                item.reason = f"已存在相同图片，ID {by_sha[digest]}"
            elif digest in seen_hashes:
                item.status = "duplicate"
                item.duplicate_id = seen_hashes[digest]
                item.reason = "本批次内重复"
            else:
                seen_hashes[digest] = token
                item.status = "ready"
        except Exception as exc:  # noqa: BLE001 - surface every bad file in the scan
            item.status = "invalid"
            item.reason = f"{type(exc).__name__}: {exc}"
        items.append(item)
    return Scan(scan_id=uuid.uuid4().hex[:16], source_dir=None, items=items)


def scan_folder(folder: str | Path, *, root: Path | None = None, recursive: bool = True) -> Scan:
    cfg = get_config(root)
    folder_path = Path(folder).expanduser().resolve()
    if not folder_path.is_dir():
        raise ValidationError(f"not a directory: {folder_path}")
    candidates: list[Path] = []
    iterator = folder_path.rglob("*") if recursive else folder_path.iterdir()
    for path in iterator:
        if len(candidates) >= MAX_FILES:
            break
        try:
            if path.is_symlink() or not path.is_file():
                continue
            if path.name.startswith(".") or path.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
        except OSError:
            continue
        candidates.append(path)
    scan = scan_paths(sorted(candidates, key=lambda p: p.as_posix().casefold()), root=cfg.root)
    scan.source_dir = folder_path
    # Use a path relative to the chosen folder for the UI.
    if scan.source_dir:
        for item in scan.items:
            try:
                item.relative_path = item.source_path.relative_to(scan.source_dir).as_posix()
            except ValueError:
                pass
    return scan


def scan_single(path: str | Path, *, root: Path | None = None) -> Scan:
    scan = scan_paths([Path(path)], root=root)
    scan.source_dir = Path(path).resolve().parent
    return scan


def _thumbnail(source: Path, output_dir: Path, image_id: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    with Image.open(source) as opened:
        if getattr(opened, "is_animated", False):
            opened.seek(0)
        has_alpha = opened.mode in ("RGBA", "LA", "P") or "transparency" in opened.info
        image = opened.convert("RGBA" if has_alpha else "RGB")
        image = ImageOps.exif_transpose(image)
        for size in THUMB_SIZES:
            target = output_dir / str(size) / f"{image_id}.webp"
            target.parent.mkdir(parents=True, exist_ok=True)
            thumb = image.copy()
            thumb.thumbnail((size, size), Image.Resampling.LANCZOS)
            kwargs: dict[str, Any] = {
                "format": "WEBP",
                "quality": 80 if size == 320 else 84,
                "method": 6,
            }
            if has_alpha:
                kwargs["lossless"] = False
            thumb.save(target, **kwargs)
            result[str(size)] = target
    return result


def _manifest_entry(item: ScanItem, entry: dict[str, Any], source_type: str, submission_id: str | None) -> dict[str, Any]:
    now = iso_local()
    return {
        "source": source_type,
        "source_id": submission_id or f"manual-{entry['id']}",
        "name": entry["name"],
        "description": "本地管理端导入" if source_type == "manual" else "投稿审核导入",
        "category": "手动补充" if source_type == "manual" else "用户投稿",
        "kind": "GIF 动图" if item.frames > 1 else "静态图片",
        "source_label": "本地管理端" if source_type == "manual" else "公开投稿",
        "source_tags": [],
        "image_url": None,
        "page_url": "",
        "final_url": None,
        "content_type": f"image/{item.image_format}",
        "format": item.image_format,
        "width": item.width,
        "height": item.height,
        "frames": item.frames,
        "bytes": item.bytes,
        "sha256": item.sha256,
        "dhash": item.dhash,
        "downloaded_at": now,
        "added_at": now,
        "aliases": [],
    }


def _update_thumb_manifest(cfg: Config, entries: list[dict[str, Any]]) -> None:
    path = cfg.root / "img" / "thumbs" / "manifest.json"
    if path.is_file():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            manifest = {}
    else:
        manifest = {}
    if not isinstance(manifest, dict):
        manifest = {}
    items = manifest.get("entries")
    if not isinstance(items, dict):
        items = {}
    for entry in entries:
        image_id = str(entry["id"])
        converted = None
        for suffix in (".gif", ".jpg"):
            candidate = cfg.root / "img" / "converted" / f"{image_id}{suffix}"
            if candidate.is_file():
                converted = candidate
                break
        if converted is None:
            continue
        thumbs: dict[str, Any] = {}
        for size in THUMB_SIZES:
            target = cfg.root / "img" / "thumbs" / str(size) / f"{image_id}.webp"
            if not target.is_file():
                continue
            with Image.open(target) as thumb:
                width, height = thumb.size
            thumbs[str(size)] = {
                "path": target.relative_to(cfg.root).as_posix(),
                "width": width,
                "height": height,
                "bytes": target.stat().st_size,
            }
        items[image_id] = {
            "source": converted.relative_to(cfg.root).as_posix(),
            "source_format": entry.get("format"),
            "width": entry.get("width"),
            "height": entry.get("height"),
            "thumbs": thumbs,
        }
    ordered = {key: items[key] for key in sorted(items, key=lambda value: int(value))}
    payload = {
        "schema_version": 1,
        "generated_at": iso_local(),
        "source": "img/converted",
        "sizes": list(THUMB_SIZES),
        "count": len(ordered),
        "entries": ordered,
    }
    # Preserve failures from the full rebuild tool when present.
    if isinstance(manifest.get("failures"), list):
        payload["failures"] = manifest["failures"]
    atomic_write_json(path, payload)


def _write_audit(cfg: Config, event: dict[str, Any]) -> None:
    target = cfg.root / "data" / "admin" / "audit.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    event = {"at": iso_local(), **event}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _normalise_tags(values: Any) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValidationError("tag must be an array")
    result: list[str] = []
    for value in values:
        tag = clean_tag(str(value))
        if tag not in result:
            result.append(tag)
    return result


def commit_scan(
    scan: Scan,
    selections: list[dict[str, Any]],
    *,
    root: Path | None = None,
    source_type: str = "manual",
    submission_id: str | None = None,
    status: str = "draft",
) -> dict[str, Any]:
    """Import selected scan items as drafts.

    IDs are allocated only here, under the library file lock.  Failure of one
    item never causes its id to be reused by a later import.
    """

    if source_type not in {"manual", "submission"}:
        raise ValidationError("invalid source_type")
    if status not in {"draft", "published", "blocked"}:
        raise ValidationError("invalid status")
    cfg = get_config(root)
    by_token = {item.token: item for item in scan.items}
    selected = []
    for raw in selections:
        token = str(raw.get("token", ""))
        item = by_token.get(token)
        if item is None or item.status != "ready":
            continue
        name = clean_name(str(raw.get("name") or item.display_name)) or item.display_name
        tags = _normalise_tags(raw.get("tag", []))
        selected.append((item, name[:120], tags))
    if not selected:
        raise ValidationError("没有可导入的图片")
    if len(selected) > MAX_FILES:
        raise ValidationError(f"too many images selected (max {MAX_FILES})")

    imported: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    with FileLock(cfg.lock_path, "meme library"):
        catalog = load_catalog(cfg)
        state = load_state(cfg)
        existing: set[int] = set(ids_from_catalog(catalog, cfg.id_max_digits))
        existing.update(ids_from_image_dir(cfg.image_dir, cfg.id_max_digits))
        existing.update(ids_from_image_dir(cfg.root / "img" / "converted", cfg.id_max_digits))
        ids, next_id = allocate_ids(
            len(selected),
            existing_numbers=existing,
            next_id=int(state.get("next_id") or 1),
            min_width=cfg.id_min_width,
            max_digits=cfg.id_max_digits,
        )
        # Persist the high-water mark before touching files.  If this batch
        # fails, these ids remain burned and can never collide with a retry.
        state.update(
            {
                "schema_version": SCHEMA_VERSION,
                "next_id": next_id,
                "updated_at": iso_local(),
            }
        )
        save_state(cfg, state)

        for (item, name, tags), image_id in zip(selected, ids, strict=False):
            raw_target = cfg.image_dir / f"{image_id}.{item.extension}"
            converted_dir = cfg.root / "img" / "converted"
            converted_dir.mkdir(parents=True, exist_ok=True)
            created: list[Path] = []
            try:
                if raw_target.exists():
                    raise ValidationError(f"raw file already exists: {raw_target.name}")
                shutil.copy2(item.source_path, raw_target)
                created.append(raw_target)
                conversion = convert_one(
                    raw_target,
                    converted_dir,
                    jpeg_quality=90,
                    background=(255, 255, 255),
                    copy_jpeg=True,
                    force=False,
                )
                if conversion.status == "failed":
                    raise ValidationError(conversion.error or "conversion failed")
                converted = converted_dir / f"{image_id}{'.gif' if conversion.animated else '.jpg'}"
                if not converted.is_file():
                    raise ValidationError("converted image was not created")
                created.append(converted)
                thumbs = _thumbnail(converted, cfg.root / "img" / "thumbs", image_id)
                created.extend(thumbs.values())
                entry = {
                    "id": image_id,
                    "name": name,
                    "tag": tags,
                    "file": cfg.rel(raw_target),
                    "format": item.image_format,
                    "width": item.width,
                    "height": item.height,
                    "bytes": item.bytes,
                    "sha256": item.sha256,
                    "source": source_type,
                    "source_type": source_type,
                    "source_id": submission_id or f"manual-{image_id}",
                    "source_url": None,
                    "submission_id": submission_id,
                    "status": status,
                    "added_at": iso_local(),
                }
                catalog.append(compact_catalog_entry(entry))
                imported.append(
                    {
                        "id": image_id,
                        "name": name,
                        "tag": tags,
                        "status": status,
                        "thumb_320_url": f"/api/admin/media/{image_id}/thumb/320",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - report and clean this item
                failed.append({"token": item.token, "name": name, "error": f"{type(exc).__name__}: {exc}"})
                for path in reversed(created):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass

        if imported:
            validate_catalog(catalog, cfg)
            run_id = new_run_id()
            digest = save_catalog(cfg, catalog, run_id)
            state.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "count": len(catalog),
                    "catalog_sha256": digest,
                    "last_run_id": run_id,
                    "updated_at": iso_local(),
                }
            )
            save_state(cfg, state)
            manifest = load_manifest(cfg)
            for entry in catalog:
                if str(entry["id"]) not in {item["id"] for item in imported}:
                    continue
                item = next((candidate for candidate in scan.items if candidate.token in {
                    selection.get("token") for selection in selections
                } and candidate.sha256 == entry.get("sha256")), None)
                if item is None:
                    continue
                manifest.setdefault("items", {})[str(entry["id"])] = _manifest_entry(item, entry, source_type, submission_id)
            save_manifest(cfg, manifest, run_id)
            _update_thumb_manifest(cfg, [entry for entry in catalog if str(entry["id"]) in {item["id"] for item in imported}])
            _write_audit(
                cfg,
                {
                    "action": "import",
                    "batch": scan.scan_id,
                    "ids": [item["id"] for item in imported],
                    "source": "submission" if source_type == "submission" else ("manual_folder" if scan.source_dir else "manual_single"),
                },
            )
    return {"imported": imported, "failed": failed, "next_id": next_id if imported else None}


def edit_image(
    image_id: str,
    *,
    name: str | None = None,
    tags: list[str] | None = None,
    status: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    cfg = get_config(root)
    with FileLock(cfg.lock_path, "meme library"):
        catalog = load_catalog(cfg)
        entry = next((item for item in catalog if str(item.get("id")) == str(image_id)), None)
        if entry is None:
            raise ValidationError(f"image not found: {image_id}")
        before = {"name": entry.get("name"), "tag": list(entry.get("tag", [])), "status": entry.get("status", "published")}
        if name is not None:
            cleaned = clean_name(name)
            if not cleaned:
                raise ValidationError("name must not be empty")
            entry["name"] = cleaned[:120]
        if tags is not None:
            entry["tag"] = _normalise_tags(tags)
        if status is not None:
            if status not in {"published", "draft", "blocked"}:
                raise ValidationError("invalid status")
            entry["status"] = status
        validate_catalog(catalog, cfg)
        run_id = new_run_id()
        digest = save_catalog(cfg, catalog, run_id)
        state = load_state(cfg)
        state.update(
            {
                "schema_version": SCHEMA_VERSION,
                "count": len(catalog),
                "catalog_sha256": digest,
                "last_run_id": run_id,
                "updated_at": iso_local(),
            }
        )
        save_state(cfg, state)
        _write_audit(
            cfg,
            {
                "action": "edit",
                "id": str(image_id),
                "before": before,
                "after": {
                    "name": entry.get("name"),
                    "tag": list(entry.get("tag", [])),
                    "status": entry.get("status", "published"),
                },
            },
        )
        return compact_catalog_entry(entry)


def list_tags(cfg: Config) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for entry in load_catalog(cfg):
        for tag in entry.get("tag", []):
            counts[str(tag)] = counts.get(str(tag), 0) + 1
    source = cfg.root / "frontend" / "src" / "data" / "tag-categories.json"
    categories: dict[str, list[str]] = {}
    if source.is_file():
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                categories = {str(key): [str(v) for v in value] for key, value in raw.items() if isinstance(value, list)}
        except (OSError, json.JSONDecodeError):
            categories = {}
    return {"tags": counts, "categories": categories}


def list_images(
    cfg: Config,
    *,
    q: str = "",
    tag: str = "",
    status: str = "",
    missing: str = "",
    limit: int = 60,
    offset: int = 0,
) -> dict[str, Any]:
    catalog = load_catalog(cfg)
    query = q.strip().casefold()
    if query:
        catalog = [
            entry
            for entry in catalog
            if query in str(entry.get("name", "")).casefold()
            or query in str(entry.get("id", "")).casefold()
            or any(query in str(value).casefold() for value in entry.get("tag", []))
        ]
    if tag:
        catalog = [entry for entry in catalog if tag in entry.get("tag", [])]
    if status:
        catalog = [entry for entry in catalog if str(entry.get("status", "published")) == status]
    if missing == "name":
        catalog = [entry for entry in catalog if not str(entry.get("name", "")).strip()]
    elif missing == "tag":
        catalog = [entry for entry in catalog if not entry.get("tag")]
    catalog.sort(key=lambda entry: int(entry["id"]), reverse=True)
    total = len(catalog)
    page = catalog[offset : offset + limit]
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "items": [_image_view(entry) for entry in page],
    }


def _image_view(entry: dict[str, Any]) -> dict[str, Any]:
    image_id = str(entry["id"])
    return {
        "id": image_id,
        "name": entry.get("name", ""),
        "tag": list(entry.get("tag", [])),
        "format": entry.get("format"),
        "width": entry.get("width"),
        "height": entry.get("height"),
        "bytes": entry.get("bytes"),
        "source": entry.get("source"),
        "source_type": entry.get("source_type", "manual" if entry.get("source") == "manual" else "crawl"),
        "status": entry.get("status", "published"),
        "added_at": entry.get("added_at"),
        "image_url": f"/api/admin/media/{image_id}",
        "thumb_320_url": f"/api/admin/media/{image_id}/thumb/320",
        "thumb_640_url": f"/api/admin/media/{image_id}/thumb/640",
    }


def stats(cfg: Config) -> dict[str, Any]:
    catalog = load_catalog(cfg)
    state = load_state(cfg)
    statuses = {"published": 0, "draft": 0, "blocked": 0}
    for entry in catalog:
        statuses[str(entry.get("status", "published"))] = statuses.get(str(entry.get("status", "published")), 0) + 1
    tags = {tag for entry in catalog for tag in entry.get("tag", [])}
    return {
        "total": len(catalog),
        "published": statuses.get("published", 0),
        "draft": statuses.get("draft", 0),
        "blocked": statuses.get("blocked", 0),
        "missing_name": sum(1 for entry in catalog if not str(entry.get("name", "")).strip()),
        "missing_tag": sum(1 for entry in catalog if not entry.get("tag")),
        "tags": len(tags),
        "max_id": max((int(entry["id"]) for entry in catalog), default=0),
        "next_id": int(state.get("next_id") or 1),
        "local_only": True,
    }


def entry_for_media(cfg: Config, image_id: str) -> dict[str, Any] | None:
    for entry in load_catalog(cfg):
        if str(entry.get("id")) == str(image_id):
            return entry
    return None


def raw_or_converted(cfg: Config, image_id: str) -> Path | None:
    converted = cfg.root / "img" / "converted"
    for suffix in (".gif", ".jpg"):
        candidate = converted / f"{image_id}{suffix}"
        if candidate.is_file():
            return candidate
    entry = entry_for_media(cfg, image_id)
    if entry:
        path = cfg.root / entry.get("file", "")
        if path.is_file():
            return path
    return None
