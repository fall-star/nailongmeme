from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from . import SCHEMA_VERSION
from .config import Config
from .ids import ids_from_catalog, ids_from_image_dir, parse_id
from .imaging import FORMAT_EXT, extension_for
from .util import (
    ValidationError,
    atomic_write_json,
    iso_local,
    json_bytes,
    prune_backups,
    read_json,
    sha256_bytes,
    sha256_file,
    short,
    utc_stamp,
)

REQUIRED_FIELDS = (
    "id",
    "name",
    "tag",
    "file",
    "format",
    "sha256",
    "source",
    "source_url",
    "added_at",
)

_HEX = set("0123456789abcdef")


def _is_hex_digest(value: str) -> bool:
    return len(value) == 64 and set(value) <= _HEX


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_entry_shape(entry: Any, cfg: Config, *, where: str = "entry") -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise ValidationError(f"{where}: must be an object")
    for field in REQUIRED_FIELDS:
        if field not in entry:
            raise ValidationError(f"{where}: missing required field {field!r}")
    number = parse_id(entry["id"], cfg.id_max_digits)

    name = entry["name"]
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"{where}: name must be a non-empty string")

    tags = entry["tag"]
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValidationError(f"{where}: tag must be an array of strings")
    seen_tags: set[str] = set()
    for tag in tags:
        if not tag.strip():
            raise ValidationError(f"{where}: tag entries must not be empty")
        if tag != tag.strip():
            raise ValidationError(f"{where}: tag {tag!r} has leading/trailing whitespace")
        if tag in seen_tags:
            raise ValidationError(f"{where}: duplicate tag {tag!r}")
        seen_tags.add(tag)

    file_value = entry["file"]
    if not isinstance(file_value, str) or not file_value:
        raise ValidationError(f"{where}: file must be a non-empty string")
    pure = PurePosixPath(file_value)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValidationError(f"{where}: file path must be relative and stay inside the project")
    image_prefix = cfg.rel(cfg.image_dir)
    if pure.parent.as_posix() != image_prefix:
        raise ValidationError(
            f"{where}: file must live directly in {image_prefix}/, got {file_value!r}"
        )

    image_format = entry["format"]
    if not isinstance(image_format, str) or image_format not in FORMAT_EXT:
        raise ValidationError(f"{where}: unsupported format {image_format!r}")
    expected_ext = extension_for(image_format)
    expected_name = f"{entry['id']}.{expected_ext}"
    if pure.name != expected_name:
        raise ValidationError(
            f"{where}: file must be named {expected_name!r} for id {entry['id']!r}, "
            f"got {pure.name!r}"
        )

    digest = entry["sha256"]
    if not isinstance(digest, str) or not _is_hex_digest(digest):
        raise ValidationError(f"{where}: sha256 must be 64 lowercase hex characters")

    source = entry["source"]
    if not isinstance(source, str) or not source.strip():
        raise ValidationError(f"{where}: source must be a non-empty string")

    source_url = entry["source_url"]
    if not isinstance(source_url, str) or not source_url.startswith(("http://", "https://")):
        raise ValidationError(f"{where}: source_url must be an http(s) URL")

    added_at = entry["added_at"]
    if not isinstance(added_at, str):
        raise ValidationError(f"{where}: added_at must be an ISO-8601 string")
    from .util import parse_iso

    try:
        parse_iso(added_at)
    except ValueError as exc:
        raise ValidationError(f"{where}: added_at is not ISO-8601: {added_at!r}") from exc

    for key in ("width", "height"):
        if key in entry and entry[key] is not None:
            if not _is_int(entry[key]) or entry[key] <= 0:
                raise ValidationError(f"{where}: {key} must be a positive integer or null")
    if "bytes" in entry and entry["bytes"] is not None:
        if not _is_int(entry["bytes"]) or entry["bytes"] <= 0:
            raise ValidationError(f"{where}: bytes must be a positive integer or null")
    if "source_id" in entry and not isinstance(entry["source_id"], str):
        raise ValidationError(f"{where}: source_id must be a string when present")

    return entry


def validate_catalog(entries: Any, cfg: Config) -> list[dict[str, Any]]:
    if not isinstance(entries, list):
        raise ValidationError(f"{cfg.catalog}: top-level value must be an array")
    seen_ids: dict[int, str] = {}
    seen_sha: dict[str, str] = {}
    seen_files: dict[str, str] = {}
    for index, entry in enumerate(entries):
        validate_entry_shape(entry, cfg, where=f"catalog[{index}]")
        number = parse_id(entry["id"], cfg.id_max_digits)
        if number in seen_ids:
            raise ValidationError(
                f"catalog[{index}]: id {entry['id']!r} duplicates {seen_ids[number]!r}"
            )
        seen_ids[number] = entry["id"]
        digest = entry["sha256"]
        if digest in seen_sha:
            raise ValidationError(
                f"catalog[{index}]: sha256 duplicates id {seen_sha[digest]!r}; "
                "identical images must not be stored twice"
            )
        seen_sha[digest] = entry["id"]
        file_value = entry["file"]
        if file_value in seen_files:
            raise ValidationError(
                f"catalog[{index}]: file {file_value!r} is used by id {seen_files[file_value]!r}"
            )
        seen_files[file_value] = entry["id"]
    return entries


def load_catalog(cfg: Config) -> list[dict[str, Any]]:
    if not cfg.catalog.exists():
        return []
    data = read_json(cfg.catalog)
    return validate_catalog(data, cfg)


def backup_file(cfg: Config, path: Path, run_id: str, label: str) -> Path | None:
    if not path.exists():
        return None
    cfg.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = utc_stamp()
    target = cfg.backup_dir / f"{label}-{stamp}-{run_id}.json"
    counter = 1
    while target.exists():
        target = cfg.backup_dir / f"{label}-{stamp}-{run_id}-{counter}.json"
        counter += 1
    shutil.copy2(path, target)
    prune_backups(cfg.backup_dir, f"{label}-*.json", cfg.backup_keep)
    return target


def save_catalog(
    cfg: Config,
    entries: list[dict[str, Any]],
    run_id: str,
    *,
    backup: bool = True,
) -> str:
    validate_catalog(entries, cfg)
    ordered = [
        compact_catalog_entry(item)
        for item in sorted(entries, key=lambda entry: int(entry["id"], 10))
    ]
    if backup:
        backup_file(cfg, cfg.catalog, run_id, "memes")
    atomic_write_json(cfg.catalog, ordered)
    # The user-facing delivery catalogs intentionally contain only these three
    # fields.  They are regenerated on every crawl/tag/id change so they cannot
    # silently drift from the full source catalog.
    delivery = [
        {"id": entry["id"], "name": entry["name"], "tag": list(entry["tag"])}
        for entry in ordered
    ]
    written: set[Path] = set()
    for delivery_path in (cfg.delivery_catalog, cfg.delivery_catalog_copy):
        if delivery_path is None or delivery_path == cfg.catalog or delivery_path in written:
            continue
        atomic_write_json(delivery_path, delivery)
        written.add(delivery_path)
    return sha256_bytes(json_bytes(ordered))


def catalog_sha256(cfg: Config) -> str | None:
    if not cfg.catalog.exists():
        return None
    return sha256_file(cfg.catalog)


def default_manifest() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "updated_at": iso_local(),
        "items": {},
    }


def load_manifest(cfg: Config) -> dict[str, Any]:
    if not cfg.manifest.exists():
        return default_manifest()
    data = read_json(cfg.manifest)
    if not isinstance(data, dict) or not isinstance(data.get("items"), dict):
        raise ValidationError(f"{cfg.manifest}: expected an object with an 'items' object")
    for key, value in data["items"].items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise ValidationError(f"{cfg.manifest}: items must map id strings to objects")
        parse_id(key, cfg.id_max_digits)
    return data


def save_manifest(cfg: Config, manifest: dict[str, Any], run_id: str) -> None:
    items = manifest.get("items")
    if not isinstance(items, dict):
        raise ValidationError("manifest.items must be an object")
    ordered = {key: items[key] for key in sorted(items, key=lambda value: int(value, 10))}
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": iso_local(),
        "items": ordered,
    }
    backup_file(cfg, cfg.manifest, run_id, "manifest")
    atomic_write_json(cfg.manifest, payload)


def default_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "next_id": 1,
        "count": 0,
        "catalog_sha256": None,
        "last_run_id": None,
        "updated_at": iso_local(),
    }


def load_state(cfg: Config) -> dict[str, Any]:
    if not cfg.state.exists():
        return default_state()
    data = read_json(cfg.state)
    if not isinstance(data, dict):
        raise ValidationError(f"{cfg.state}: top-level value must be an object")
    return data


def save_state(cfg: Config, state: dict[str, Any]) -> None:
    atomic_write_json(cfg.state, state)


def default_sources() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "sources": {}}


def load_sources(cfg: Config) -> dict[str, Any]:
    if not cfg.sources.exists():
        return default_sources()
    data = read_json(cfg.sources)
    if not isinstance(data, dict) or not isinstance(data.get("sources"), dict):
        raise ValidationError(f"{cfg.sources}: expected an object with a 'sources' object")
    return data


def save_sources(cfg: Config, registry: dict[str, Any], run_id: str) -> None:
    backup_file(cfg, cfg.sources, run_id, "sources")
    atomic_write_json(cfg.sources, registry)


def state_from_catalog(cfg: Config, entries: list[dict[str, Any]], *, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog_ids = ids_from_catalog(entries, cfg.id_max_digits)
    file_ids = ids_from_image_dir(cfg.image_dir, cfg.id_max_digits)
    highest = max([*catalog_ids, *file_ids], default=0)
    previous_next = 1
    if previous:
        value = previous.get("next_id")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            previous_next = value
    next_id = max(previous_next, highest + 1, 1)
    if next_id > cfg.id_limit + 1:
        next_id = cfg.id_limit + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "next_id": next_id,
        "count": len(entries),
        "catalog_sha256": catalog_sha256(cfg),
        "last_run_id": previous.get("last_run_id") if previous else None,
        "updated_at": iso_local(),
    }


def index_manifest(manifest: dict[str, Any]) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    by_source: dict[tuple[str, str], str] = {}
    by_url: dict[str, str] = {}
    for entry_id, item in manifest.get("items", {}).items():
        source = item.get("source")
        source_id = item.get("source_id")
        if isinstance(source, str) and isinstance(source_id, str) and source_id:
            by_source[(source, source_id)] = entry_id
        url = item.get("image_url") or item.get("source_url")
        if isinstance(url, str) and url:
            by_url[url] = entry_id
        for alias in item.get("aliases", []) or []:
            if not isinstance(alias, dict):
                continue
            alias_source = alias.get("source")
            alias_id = alias.get("source_id")
            if isinstance(alias_source, str) and isinstance(alias_id, str) and alias_id:
                by_source[(alias_source, alias_id)] = entry_id
            alias_url = alias.get("image_url") or alias.get("source_url")
            if isinstance(alias_url, str) and alias_url:
                by_url[alias_url] = entry_id
    return by_source, by_url


def catalog_index(entries: Iterable[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[tuple[str, str], dict[str, Any]]]:
    by_id: dict[str, dict[str, Any]] = {}
    by_sha: dict[str, dict[str, Any]] = {}
    by_source: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        by_id[entry["id"]] = entry
        by_sha[entry["sha256"]] = entry
        source = entry.get("source")
        source_id = entry.get("source_id")
        if isinstance(source, str) and isinstance(source_id, str) and source_id:
            by_source[(source, source_id)] = entry
    return by_id, by_sha, by_source


def compact_catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Stable field order for human-friendly diffs."""
    order = [
        "id",
        "name",
        "tag",
        "file",
        "format",
        "width",
        "height",
        "bytes",
        "sha256",
        "source",
        "source_id",
        "source_url",
        "source_page",
        "added_at",
    ]
    result = {key: entry[key] for key in order if key in entry}
    for key, value in entry.items():
        if key not in result:
            result[key] = value
    return result


def summarize_entries(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return "0 entries"
    formats: dict[str, int] = {}
    for entry in entries:
        formats[entry["format"]] = formats.get(entry["format"], 0) + 1
    format_text = ", ".join(f"{key}={value}" for key, value in sorted(formats.items()))
    return f"{len(entries)} entries ({format_text})"


def describe_entry(entry: dict[str, Any]) -> str:
    return f"{entry['id']}: {short(entry['name'], 60)} [{entry['format']}]"
