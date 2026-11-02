from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any, Iterable

from . import SCHEMA_VERSION
from .catalog import (
    load_catalog,
    load_manifest,
    load_state,
    save_catalog,
    save_manifest,
    save_state,
)
from .config import Config
from .ids import format_id
from .imaging import extension_for
from .util import (
    FileLock,
    ValidationError,
    atomic_write_json,
    clean_name,
    human_bytes,
    iso_local,
    new_run_id,
    read_json,
    sha256_file,
    short,
)

MAX_TAG_LENGTH = 64


def clean_tag(value: str) -> str:
    tag = clean_name(value)
    if not tag:
        raise ValidationError("tags must not be empty")
    if len(tag) > MAX_TAG_LENGTH:
        raise ValidationError(f"tag {short(tag, 40)!r} is longer than {MAX_TAG_LENGTH} characters")
    if "," in tag:
        raise ValidationError(f"tag {tag!r} contains a comma; commas separate --set values")
    return tag


def _parse_selection(spec: str) -> set[int]:
    numbers: set[int] = set()
    for raw in spec.split(","):
        part = raw.strip()
        if not part:
            continue
        if "-" in part:
            left, _, right = part.partition("-")
            try:
                start = int(left.strip(), 10)
                end = int(right.strip(), 10)
            except ValueError as exc:
                raise ValidationError(f"invalid id range {part!r}") from exc
            if start < 1 or end < start:
                raise ValidationError(f"invalid id range {part!r}")
            if end - start > 100_000:
                raise ValidationError(f"id range {part!r} is too large")
            numbers.update(range(start, end + 1))
        else:
            try:
                number = int(part, 10)
            except ValueError as exc:
                raise ValidationError(f"invalid id {part!r}") from exc
            if number < 1:
                raise ValidationError(f"id must be >= 1, got {number}")
            numbers.add(number)
    if not numbers:
        raise ValidationError("no ids selected")
    return numbers


def select_entries(catalog: list[dict[str, Any]], spec: str | None, all_entries: bool) -> list[dict[str, Any]]:
    if all_entries and spec:
        raise ValidationError("--all and --ids are mutually exclusive")
    if all_entries:
        return list(catalog)
    if not spec:
        raise ValidationError("select entries with --all or --ids")
    numbers = _parse_selection(spec)
    by_number = {int(entry["id"], 10): entry for entry in catalog}
    missing = sorted(numbers - set(by_number))
    if missing:
        raise ValidationError(f"ids not found in catalog: {', '.join(map(str, missing[:20]))}")
    return [by_number[number] for number in sorted(numbers)]


@dataclass(slots=True)
class TagResult:
    changed: int
    selected: int
    dry_run: bool
    changes: list[dict[str, Any]]


def apply_tags(
    config: Config,
    *,
    ids_spec: str | None,
    all_entries: bool,
    add: Iterable[str] = (),
    remove: Iterable[str] = (),
    set_tags: str | None = None,
    dry_run: bool = False,
) -> TagResult:
    add_tags = list(dict.fromkeys(clean_tag(value) for value in add))
    remove_tags = list(dict.fromkeys(clean_tag(value) for value in remove))
    set_list: list[str] | None = None
    if set_tags is not None:
        if add_tags or remove_tags:
            raise ValidationError("--set cannot be combined with --add or --remove")
        if set_tags.strip():
            set_list = list(dict.fromkeys(clean_tag(value) for value in set_tags.split(",")))
        else:
            set_list = []
    elif not add_tags and not remove_tags:
        raise ValidationError("nothing to do: pass --add, --remove or --set")

    with FileLock(config.lock_path, "meme library"):
        catalog = load_catalog(config)
        selected = select_entries(catalog, ids_spec, all_entries)
        changes: list[dict[str, Any]] = []
        for entry in selected:
            before = list(entry["tag"])
            after = list(before)
            if set_list is not None:
                after = list(set_list)
            else:
                for tag in add_tags:
                    if tag not in after:
                        after.append(tag)
                if remove_tags:
                    after = [tag for tag in after if tag not in set(remove_tags)]
            if after != before:
                entry["tag"] = after
                changes.append(
                    {
                        "id": entry["id"],
                        "name": entry["name"],
                        "before": before,
                        "after": after,
                    }
                )
        if changes and not dry_run:
            run_id = new_run_id()
            digest = save_catalog(config, catalog, run_id)
            state = load_state(config)
            state.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "count": len(catalog),
                    "catalog_sha256": digest,
                    "next_id": max(
                        int(state.get("next_id") or 1),
                        max((int(entry["id"], 10) for entry in catalog), default=0) + 1,
                    ),
                    "last_run_id": run_id,
                    "updated_at": iso_local(),
                }
            )
            save_state(config, state)
        return TagResult(
            changed=len(changes),
            selected=len(selected),
            dry_run=dry_run,
            changes=changes,
        )


@dataclass(slots=True)
class RepadResult:
    changed: int
    dry_run: bool
    applied: bool
    mappings: list[tuple[str, str, str, str]]


def reformat_ids(
    config: Config,
    *,
    width: int,
    dry_run: bool = False,
    yes: bool = False,
) -> RepadResult:
    """Change the textual zero-padding of ids while preserving numeric identity."""
    if width < 0 or width > config.id_max_digits:
        raise ValidationError(
            f"width must be between 0 and {config.id_max_digits}, got {width}"
        )
    with FileLock(config.lock_path, "meme library"):
        catalog = load_catalog(config)
        manifest = load_manifest(config)
        if not catalog:
            raise ValidationError("catalog is empty; nothing to reformat")

        image_prefix = config.rel(config.image_dir)
        mappings: list[tuple[str, str, str, str]] = []
        targets: dict[str, str] = {}
        for entry in catalog:
            new_id = format_id(int(entry["id"], 10), width, config.id_max_digits)
            new_file = f"{image_prefix}/{new_id}.{extension_for(entry['format'])}"
            new_path = config.root / new_file
            old_path = config.root / entry["file"]
            if new_id == entry["id"] and new_file == entry["file"]:
                continue
            if new_path.exists() and new_path != old_path:
                raise ValidationError(
                    f"refusing to overwrite existing file {new_file} while reformatting id {entry['id']}"
                )
            if new_file in targets:
                raise ValidationError(f"multiple ids would map to {new_file}")
            targets[new_file] = entry["id"]
            mappings.append((entry["id"], new_id, entry["file"], new_file))

        if not mappings:
            if not dry_run and yes and width != config.id_min_width:
                raw_config = (
                    copy.deepcopy(read_json(config.config_path))
                    if config.config_path.exists()
                    else {}
                )
                raw_config.setdefault("ids", {})["min_width"] = width
                atomic_write_json(config.config_path, raw_config)
            return RepadResult(changed=0, dry_run=dry_run, applied=False, mappings=[])

        if dry_run or not yes:
            print(f"ID reformat plan ({len(mappings)} changes, width={width}):")
            for old_id, new_id, old_file, new_file in mappings[:30]:
                print(f"  {old_id:>6} -> {new_id:<6}  {old_file} -> {new_file}")
            if len(mappings) > 30:
                print(f"  ... {len(mappings) - 30} more")
            if not dry_run:
                print("Nothing was written. Re-run with --yes to apply.")
            return RepadResult(
                changed=len(mappings), dry_run=dry_run, applied=False, mappings=mappings
            )

        run_id = new_run_id()
        mapping_by_old = {old_id: new_id for old_id, new_id, _, _ in mappings}
        renamed: list[tuple[str, str]] = []
        original_catalog = copy.deepcopy(catalog)
        original_manifest = copy.deepcopy(manifest)
        original_state = copy.deepcopy(load_state(config))
        old_config_data = (
            copy.deepcopy(read_json(config.config_path)) if config.config_path.exists() else {}
        )
        new_config_data = copy.deepcopy(old_config_data)
        ids_config = new_config_data.setdefault("ids", {})
        ids_config["min_width"] = width
        try:
            for _old_id, _new_id, old_file, new_file in mappings:
                old_path = config.root / old_file
                new_path = config.root / new_file
                if not old_path.exists():
                    raise ValidationError(f"source image is missing: {old_file}")
                os.replace(old_path, new_path)
                renamed.append((old_file, new_file))
            for entry in catalog:
                if entry["id"] in mapping_by_old:
                    entry["id"] = mapping_by_old[entry["id"]]
                    entry["file"] = f"{image_prefix}/{entry['id']}.{extension_for(entry['format'])}"
            items = manifest.get("items", {})
            if isinstance(items, dict):
                new_items: dict[str, Any] = {}
                for old_id, item in items.items():
                    new_id = mapping_by_old.get(old_id, old_id)
                    if isinstance(item, dict):
                        item["id"] = new_id
                    new_items[new_id] = item
                manifest["items"] = new_items
            # Keep future allocations in the same width.
            atomic_write_json(config.config_path, new_config_data)
            save_manifest(config, manifest, run_id)
            digest = save_catalog(config, catalog, run_id)
            state = load_state(config)
            state.update(
                {
                    "schema_version": SCHEMA_VERSION,
                    "count": len(catalog),
                    "catalog_sha256": digest,
                    "next_id": max(
                        int(state.get("next_id") or 1),
                        max((int(entry["id"], 10) for entry in catalog), default=0) + 1,
                    ),
                    "last_run_id": run_id,
                    "updated_at": iso_local(),
                }
            )
            save_state(config, state)
        except BaseException:
            # Best-effort in-process rollback; backups are written by save_manifest/save_catalog.
            for old_file, new_file in reversed(renamed):
                old_path = config.root / old_file
                new_path = config.root / new_file
                try:
                    if new_path.exists() and not old_path.exists():
                        os.replace(new_path, old_path)
                except OSError:
                    pass
            # Restore the JSON side as well, so a failed migration cannot leave
            # the catalog pointing at renamed files.
            try:
                atomic_write_json(config.manifest, original_manifest)
                atomic_write_json(config.catalog, original_catalog)
                atomic_write_json(config.state, original_state)
            except OSError:
                pass
            try:
                if old_config_data:
                    atomic_write_json(config.config_path, old_config_data)
            except OSError:
                pass
            raise
        return RepadResult(changed=len(mappings), dry_run=False, applied=True, mappings=mappings)


@dataclass(slots=True)
class Stats:
    count: int
    total_bytes: int
    formats: dict[str, int]
    sources: dict[str, int]
    tags: dict[str, int]
    next_id: int | None
    first_added: str | None
    last_added: str | None


def collect_stats(config: Config) -> Stats:
    catalog = load_catalog(config)
    formats: dict[str, int] = {}
    sources: dict[str, int] = {}
    tags: dict[str, int] = {}
    total_bytes = 0
    added: list[str] = []
    for entry in catalog:
        formats[entry["format"]] = formats.get(entry["format"], 0) + 1
        sources[entry["source"]] = sources.get(entry["source"], 0) + 1
        for tag in entry["tag"]:
            tags[tag] = tags.get(tag, 0) + 1
        if isinstance(entry.get("bytes"), int):
            total_bytes += entry["bytes"]
        if entry.get("added_at"):
            added.append(entry["added_at"])
    state = load_state(config)
    return Stats(
        count=len(catalog),
        total_bytes=total_bytes,
        formats=formats,
        sources=sources,
        tags=tags,
        next_id=state.get("next_id"),
        first_added=min(added) if added else None,
        last_added=max(added) if added else None,
    )


def describe_stats(stats: Stats) -> str:
    lines = [
        f"Catalog: {stats.count} meme(s), {human_bytes(stats.total_bytes)}",
        f"Next id: {stats.next_id}",
        "Formats: " + (", ".join(f"{key}={value}" for key, value in sorted(stats.formats.items())) or "-"),
        "Sources: " + (", ".join(f"{key}={value}" for key, value in sorted(stats.sources.items())) or "-"),
        f"Tagged entries: {sum(stats.tags.values())} tag assignment(s), {len(stats.tags)} distinct tag(s)",
    ]
    if stats.first_added and stats.last_added:
        lines.append(f"Added between: {stats.first_added} and {stats.last_added}")
    return "\n".join(lines)
