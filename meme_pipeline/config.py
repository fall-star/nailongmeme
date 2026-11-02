from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util import ValidationError, ensure_within, read_json

DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "paths": {
        "catalog": "data/catalog.json",
        "delivery_catalog": "memes.json",
        "delivery_catalog_copy": "img/converted/memes.json",
        "image_dir": "img/raw",
        "manifest": "data/manifest.json",
        "state": "data/state.json",
        "sources": "data/sources.json",
        "snapshot_dir": "data/snapshots",
        "report_dir": "reports",
        "backup_dir": "data/backups",
        "staging_dir": "data/staging",
        "journal_dir": "data/journal",
        "log_dir": "data/logs",
    },
    "ids": {"min_width": 0, "max_digits": 5},
    "download": {
        "workers": 8,
        "timeout_seconds": 45,
        "retries": 4,
        "max_bytes": 25 * 1024 * 1024,
        "request_delay_ms": 0,
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        ),
    },
    "dedupe": {"near_duplicate_hamming": 4, "near_duplicate_max_pairs": 200},
    "backup_keep": 50,
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


@dataclass(frozen=True)
class Config:
    root: Path
    config_path: Path

    catalog: Path
    delivery_catalog: Path | None
    delivery_catalog_copy: Path | None
    image_dir: Path
    manifest: Path
    state: Path
    sources: Path
    snapshot_dir: Path
    report_dir: Path
    backup_dir: Path
    staging_dir: Path
    journal_dir: Path
    log_dir: Path

    id_min_width: int
    id_max_digits: int

    workers: int
    timeout_seconds: int
    retries: int
    max_bytes: int
    request_delay_ms: int
    user_agent: str

    near_dup_hamming: int
    near_dup_max_pairs: int
    backup_keep: int

    raw: dict[str, Any]

    @property
    def lock_path(self) -> Path:
        return self.state.parent / ".write.lock"

    @property
    def id_limit(self) -> int:
        return 10**self.id_max_digits - 1

    def ensure_dirs(self) -> None:
        for path in (
            self.image_dir,
            self.snapshot_dir,
            self.report_dir,
            self.backup_dir,
            self.staging_dir,
            self.journal_dir,
            self.log_dir,
            self.manifest.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()


def _as_int(raw: dict[str, Any], key: str, default: int, minimum: int, maximum: int) -> int:
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"pipeline.json: {key} must be an integer")
    if not minimum <= value <= maximum:
        raise ValidationError(f"pipeline.json: {key} must be between {minimum} and {maximum}")
    return value


def load_config(root: str | Path | None = None, *, create: bool = False) -> Config:
    root_path = Path(root or Path.cwd()).expanduser().resolve()
    config_path = root_path / "pipeline.json"
    raw = copy.deepcopy(DEFAULT_CONFIG)
    if config_path.exists():
        loaded = read_json(config_path)
        if not isinstance(loaded, dict):
            raise ValidationError(f"{config_path}: top-level value must be an object")
        raw = _deep_merge(raw, loaded)
    elif create:
        from .util import atomic_write_json

        atomic_write_json(config_path, raw)

    paths = raw.get("paths")
    if not isinstance(paths, dict):
        raise ValidationError("pipeline.json: paths must be an object")

    def resolved(key: str) -> Path:
        value = paths.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"pipeline.json: paths.{key} must be a non-empty string")
        candidate = (root_path / value).resolve()
        return ensure_within(root_path, candidate, f"paths.{key}")

    def resolved_optional(key: str) -> Path | None:
        value = paths.get(key, "")
        if value is None or value == "":
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"pipeline.json: paths.{key} must be a non-empty string or empty")
        candidate = (root_path / value).resolve()
        return ensure_within(root_path, candidate, f"paths.{key}")

    ids = raw.get("ids")
    download = raw.get("download")
    dedupe = raw.get("dedupe")
    if not isinstance(ids, dict) or not isinstance(download, dict) or not isinstance(dedupe, dict):
        raise ValidationError("pipeline.json: ids/download/dedupe must be objects")

    min_width = _as_int(ids, "min_width", 0, 0, 9)
    max_digits = _as_int(ids, "max_digits", 5, 1, 9)
    if min_width > max_digits:
        raise ValidationError("pipeline.json: ids.min_width cannot exceed ids.max_digits")

    cfg = Config(
        root=root_path,
        config_path=config_path,
        catalog=resolved("catalog"),
        delivery_catalog=resolved_optional("delivery_catalog"),
        delivery_catalog_copy=resolved_optional("delivery_catalog_copy"),
        image_dir=resolved("image_dir"),
        manifest=resolved("manifest"),
        state=resolved("state"),
        sources=resolved("sources"),
        snapshot_dir=resolved("snapshot_dir"),
        report_dir=resolved("report_dir"),
        backup_dir=resolved("backup_dir"),
        staging_dir=resolved("staging_dir"),
        journal_dir=resolved("journal_dir"),
        log_dir=resolved("log_dir"),
        id_min_width=min_width,
        id_max_digits=max_digits,
        workers=_as_int(download, "workers", 8, 1, 32),
        timeout_seconds=_as_int(download, "timeout_seconds", 45, 5, 300),
        retries=_as_int(download, "retries", 4, 0, 10),
        max_bytes=_as_int(download, "max_bytes", 25 * 1024 * 1024, 1024, 512 * 1024 * 1024),
        request_delay_ms=_as_int(download, "request_delay_ms", 0, 0, 60000),
        user_agent=str(download.get("user_agent", DEFAULT_CONFIG["download"]["user_agent"])),
        near_dup_hamming=_as_int(dedupe, "near_duplicate_hamming", 4, 0, 32),
        near_dup_max_pairs=_as_int(dedupe, "near_duplicate_max_pairs", 200, 0, 100000),
        backup_keep=_as_int(raw, "backup_keep", 50, 0, 10000),
        raw=raw,
    )
    return cfg
