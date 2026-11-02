from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .catalog import load_catalog, load_manifest, load_sources, load_state
from .config import Config
from .imaging import (
    FORMAT_EXT,
    extension_for,
    find_near_duplicate_pairs,
    hamming_distance_hex,
    inspect_image,
)
from .util import ValidationError, human_bytes, parse_iso, read_json, sha256_file, short

_HEX = set("0123456789abcdef")


def _is_dhash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 16 and set(value) <= _HEX


def verify_library(
    config: Config,
    *,
    quiet: bool = False,
    compute_dhash: bool = False,
) -> dict[str, Any]:
    """Audit the catalog, images, manifest and state without modifying anything."""
    errors: list[str] = []
    warnings: list[str] = []
    formats: dict[str, int] = {}
    total_bytes = 0
    checked_images = 0
    catalog: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {"items": {}}
    state: dict[str, Any] = {}
    sources: dict[str, Any] = {"sources": {}}
    near_pairs: list[dict[str, object]] = []
    near_truncated = False

    try:
        catalog = load_catalog(config)
    except (ValidationError, OSError, json.JSONDecodeError) as exc:
        errors.append(f"catalog: {exc}")
        return _finish(
            errors,
            warnings,
            catalog,
            manifest,
            state,
            formats,
            total_bytes,
            checked_images,
            near_pairs,
            near_truncated,
            quiet,
            compute_dhash,
        )

    if not catalog:
        warnings.append(f"catalog is empty or missing: {config.catalog}")

    try:
        manifest = load_manifest(config)
    except (ValidationError, OSError, json.JSONDecodeError) as exc:
        errors.append(f"manifest: {exc}")

    try:
        state = load_state(config)
    except (ValidationError, OSError, json.JSONDecodeError) as exc:
        warnings.append(f"state: {exc}")

    try:
        sources = load_sources(config)
    except (ValidationError, OSError, json.JSONDecodeError) as exc:
        warnings.append(f"sources: {exc}")

    catalog_ids = {entry["id"] for entry in catalog}
    used_files: set[str] = set()
    sha_groups: dict[str, list[str]] = {}
    dhash_items: list[tuple[str, str]] = []

    for entry in catalog:
        entry_id = entry["id"]
        if entry.get("pending") is True:
            warnings.append(
                f"id {entry_id}: pending entry has no image yet; file checks skipped"
            )
            continue
        expected_ext = extension_for(entry["format"])
        path = config.root / entry["file"]
        used_files.add(path.resolve().as_posix() if path.exists() else entry["file"])
        if not path.exists():
            errors.append(f"id {entry_id}: missing file {entry['file']}")
            continue
        if path.is_symlink():
            errors.append(f"id {entry_id}: {entry['file']} is a symlink; refusing to trust it")
            continue
        if not path.is_file():
            errors.append(f"id {entry_id}: {entry['file']} is not a regular file")
            continue
        try:
            path.resolve().relative_to(config.image_dir.resolve())
        except ValueError:
            errors.append(f"id {entry_id}: {entry['file']} escapes {config.image_dir}")
            continue

        actual_size = path.stat().st_size
        total_bytes += actual_size
        if actual_size <= 0:
            errors.append(f"id {entry_id}: empty file")
        if isinstance(entry.get("bytes"), int) and actual_size != entry["bytes"]:
            errors.append(
                f"id {entry_id}: size {actual_size} bytes does not match catalog {entry['bytes']}"
            )
        if path.suffix.lower() != f".{expected_ext}":
            errors.append(
                f"id {entry_id}: suffix {path.suffix!r} does not match format {entry['format']}"
            )

        try:
            actual_sha = sha256_file(path)
        except OSError as exc:
            errors.append(f"id {entry_id}: cannot hash file: {exc}")
            continue
        if actual_sha != entry["sha256"]:
            errors.append(
                f"id {entry_id}: sha256 mismatch (catalog {entry['sha256'][:12]}…, "
                f"disk {actual_sha[:12]}…)"
            )
        sha_groups.setdefault(actual_sha, []).append(entry_id)

        try:
            info = inspect_image(
                path,
                expected_format=entry["format"],
                with_dhash=compute_dhash,
            )
        except (ValidationError, OSError, ValueError) as exc:
            errors.append(f"id {entry_id}: {exc}")
            continue
        checked_images += 1
        formats[info.format] = formats.get(info.format, 0) + 1
        if info.format != entry["format"]:
            errors.append(
                f"id {entry_id}: catalog says {entry['format']}, file signature says {info.format}"
            )
        if isinstance(entry.get("width"), int) and info.width is not None and info.width != entry["width"]:
            warnings.append(
                f"id {entry_id}: width {info.width} does not match catalog {entry['width']}"
            )
        if isinstance(entry.get("height"), int) and info.height is not None and info.height != entry["height"]:
            warnings.append(
                f"id {entry_id}: height {info.height} does not match catalog {entry['height']}"
            )
        if compute_dhash and info.dhash:
            manifest_item = manifest.get("items", {}).get(entry_id, {})
            recorded = manifest_item.get("dhash") if isinstance(manifest_item, dict) else None
            if _is_dhash(recorded) and recorded != info.dhash:
                warnings.append(
                    f"id {entry_id}: perceptual hash changed "
                    f"(manifest {recorded}, disk {info.dhash})"
                )

        manifest_item = manifest.get("items", {}).get(entry_id)
        if isinstance(manifest_item, dict):
            recorded_dhash = manifest_item.get("dhash")
            if _is_dhash(recorded_dhash):
                dhash_items.append((entry_id, recorded_dhash))
        else:
            warnings.append(f"id {entry_id}: no manifest provenance entry")

    # Duplicate bytes should never happen in a healthy catalog.
    for digest, ids in sha_groups.items():
        if len(ids) > 1:
            errors.append(f"duplicate sha256 {digest[:12]}… shared by ids {', '.join(ids)}")

    # Orphan files are images the catalog does not reference.
    if config.image_dir.exists():
        for path in sorted(config.image_dir.iterdir()):
            if path.name.startswith(".") or not path.is_file():
                continue
            resolved = path.resolve().as_posix()
            if resolved not in used_files:
                errors.append(f"orphan image file not referenced by the catalog: {path.name}")

    # Manifest consistency.
    items = manifest.get("items", {})
    if isinstance(items, dict):
        manifest_ids = set(items)
        for missing in sorted(catalog_ids - manifest_ids, key=int):
            warnings.append(f"id {missing}: catalog entry has no manifest provenance")
        for extra in sorted(manifest_ids - catalog_ids, key=int):
            warnings.append(f"id {extra}: manifest entry has no catalog entry")
        for entry in catalog:
            item = items.get(entry["id"])
            if not isinstance(item, dict):
                continue
            if item.get("sha256") and item["sha256"] != entry["sha256"]:
                errors.append(f"id {entry['id']}: manifest sha256 disagrees with catalog")
            if item.get("format") and item["format"] != entry["format"]:
                errors.append(f"id {entry['id']}: manifest format disagrees with catalog")
            if item.get("source") and item["source"] != entry["source"]:
                errors.append(f"id {entry['id']}: manifest source disagrees with catalog")
            if item.get("aliases") is not None and not isinstance(item.get("aliases"), list):
                errors.append(f"id {entry['id']}: manifest aliases must be an array")
            for alias in item.get("aliases", []) or []:
                if not isinstance(alias, dict):
                    errors.append(f"id {entry['id']}: manifest alias is not an object")
                    break

    # State consistency.
    if isinstance(state, dict) and state:
        if state.get("count") != len(catalog):
            warnings.append(
                f"state.count is {state.get('count')!r}, catalog has {len(catalog)} entries"
            )
        next_id = state.get("next_id")
        max_id = max((int(entry["id"], 10) for entry in catalog), default=0)
        if isinstance(next_id, int) and next_id <= max_id:
            errors.append(
                f"state.next_id {next_id} would reuse an existing id (catalog max {max_id})"
            )
        recorded_sha = state.get("catalog_sha256")
        if isinstance(recorded_sha, str) and config.catalog.exists():
            actual_sha = sha256_file(config.catalog)
            if recorded_sha != actual_sha:
                warnings.append("state.catalog_sha256 does not match memes.json (manual edit?)")
    elif catalog:
        warnings.append("data/state.json is missing; run the verifier or a crawl to rebuild it")

    # The slim delivery catalogs must stay in lock-step with the full catalog.
    for delivery_path in (config.delivery_catalog, config.delivery_catalog_copy):
        if delivery_path is None or delivery_path == config.catalog:
            continue
        if not delivery_path.exists():
            errors.append(f"delivery catalog is missing: {delivery_path}")
            continue
        try:
            delivery = read_json(delivery_path)
        except (ValidationError, OSError) as exc:
            errors.append(f"delivery catalog {delivery_path}: {exc}")
            continue
        if not isinstance(delivery, list):
            errors.append(f"delivery catalog {delivery_path} must be a JSON array")
            continue
        if len(delivery) != len(catalog):
            errors.append(
                f"delivery catalog {delivery_path} has {len(delivery)} entries, "
                f"full catalog has {len(catalog)}"
            )
        by_id = {entry["id"]: entry for entry in catalog}
        seen_ids: set[str] = set()
        for index, item in enumerate(delivery):
            if not isinstance(item, dict):
                errors.append(f"delivery catalog {delivery_path}[{index}] is not an object")
                continue
            if set(item.keys()) != {"id", "name", "tag"}:
                errors.append(
                    f"delivery catalog {delivery_path}[{index}] keys must be exactly "
                    f"id/name/tag, got {sorted(item.keys())}"
                )
                continue
            entry = by_id.get(item["id"])
            if entry is None:
                errors.append(f"delivery catalog {delivery_path}[{index}] has unknown id {item['id']!r}")
                continue
            if item["id"] in seen_ids:
                errors.append(f"delivery catalog {delivery_path}[{index}] duplicates id {item['id']!r}")
            seen_ids.add(item["id"])
            if item["name"] != entry["name"]:
                errors.append(f"delivery catalog {delivery_path}: name mismatch for id {item['id']}")
            if item["tag"] != entry["tag"]:
                errors.append(f"delivery catalog {delivery_path}: tag mismatch for id {item['id']}")
        if len(seen_ids) != len(catalog):
            errors.append(
                f"delivery catalog {delivery_path} covers {len(seen_ids)} ids, "
                f"full catalog has {len(catalog)}"
            )

    # Source registry should know every source that appears in the catalog.
    registry = sources.get("sources", {}) if isinstance(sources, dict) else {}
    if isinstance(registry, dict):
        for source in sorted({entry["source"] for entry in catalog}):
            if source not in registry:
                warnings.append(f"source {source!r} is used by the catalog but not registered")
    else:
        warnings.append("sources.json has no 'sources' object")

    # Near-duplicate pairs are informational; exact duplicates are already errors.
    if dhash_items:
        near_pairs, near_truncated = find_near_duplicate_pairs(
            dhash_items,
            threshold=config.near_dup_hamming,
            limit=config.near_dup_max_pairs,
        )

    return _finish(
        errors,
        warnings,
        catalog,
        manifest,
        state,
        formats,
        total_bytes,
        checked_images,
        near_pairs,
        near_truncated,
        quiet,
        compute_dhash,
    )


def _finish(
    errors: list[str],
    warnings: list[str],
    catalog: list[dict[str, Any]],
    manifest: dict[str, Any],
    state: dict[str, Any],
    formats: dict[str, int],
    total_bytes: int,
    checked_images: int,
    near_pairs: list[dict[str, object]],
    near_truncated: bool,
    quiet: bool,
    deep: bool,
) -> dict[str, Any]:
    report = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "catalog_count": len(catalog),
        "manifest_count": len(manifest.get("items", {})) if isinstance(manifest, dict) else 0,
        "checked_images": checked_images,
        "formats": formats,
        "total_bytes": total_bytes,
        "next_id": state.get("next_id") if isinstance(state, dict) else None,
        "near_duplicate_pairs": near_pairs,
        "near_duplicate_pair_count": len(near_pairs),
        "near_duplicates_truncated": near_truncated,
        "deep": deep,
    }
    if not quiet:
        status = "OK" if report["ok"] else "FAILED"
        print(f"Library verification: {status}")
        print(f"  catalog entries: {len(catalog)}")
        print(f"  images checked:  {checked_images}")
        print(f"  total size:      {human_bytes(total_bytes)}")
        if formats:
            print(
                "  formats:         "
                + ", ".join(f"{key}={value}" for key, value in sorted(formats.items()))
            )
        if near_pairs:
            suffix = " (truncated)" if near_truncated else ""
            print(f"  near duplicates: {len(near_pairs)} pair(s){suffix}")
        print(f"  errors:          {len(errors)}")
        print(f"  warnings:        {len(warnings)}")
        for message in errors[:20]:
            print(f"  ERROR:   {short(message, 240)}")
        if len(errors) > 20:
            print(f"  ERROR:   ... {len(errors) - 20} more")
        for message in warnings[:10]:
            print(f"  WARNING: {short(message, 240)}")
        if len(warnings) > 10:
            print(f"  WARNING: ... {len(warnings) - 10} more")
    return report
