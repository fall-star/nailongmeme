from __future__ import annotations

import concurrent.futures
import contextlib
import datetime as dt
import json
import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import SCHEMA_VERSION
from .catalog import (
    catalog_index,
    compact_catalog_entry,
    index_manifest,
    load_catalog,
    load_manifest,
    load_sources,
    load_state,
    save_catalog,
    save_manifest,
    save_sources,
    save_state,
    validate_catalog,
)
from .config import Config
from .ids import allocate_ids, ids_from_catalog, ids_from_image_dir
from .imaging import find_near_duplicate_pairs, hamming_distance_hex, inspect_image
from .models import DownloadFailure, Downloaded, DuplicateRecord, FetchResult, SourceItem
from .net import HttpClient, validate_http_url
from .sources import get_source
from .util import (
    FileLock,
    PipelineError,
    ValidationError,
    atomic_write_bytes,
    atomic_write_json,
    clean_name,
    human_bytes,
    iso_local,
    new_run_id,
    now_local,
    now_utc,
    parse_iso,
    relative_posix,
    sha256_bytes,
    sha256_file,
    short,
    utc_stamp,
)

logger = logging.getLogger("meme_pipeline.crawl")


@dataclass(slots=True)
class CrawlOptions:
    source: str
    dry_run: bool = False
    limit: int | None = None
    workers: int | None = None
    refresh_metadata: bool = False
    near_dup: bool = True
    keep_staging: bool = False
    verify: bool = True


@dataclass(slots=True)
class CrawlResult:
    exit_code: int
    report: dict[str, Any]
    added: int = 0


@dataclass
class _Pending:
    index: int
    item: SourceItem
    existing_entry: dict[str, Any] | None = None


class CrawlPipeline:
    def __init__(self, config: Config, options: CrawlOptions):
        self.config = config
        self.options = options
        self.run_id = new_run_id()
        self.started_at = now_local()
        self.started_monotonic = time.monotonic()
        self.warnings: list[str] = []
        self.staging_run = config.staging_dir / self.run_id
        self.journal_path = config.journal_dir / f"{self.run_id}.json"
        self.snapshot_path: Path | None = None
        self.snapshot_sha: str | None = None
        self._catalog: list[dict[str, Any]] = []
        self._manifest: dict[str, Any] = {}
        self._state: dict[str, Any] = {}
        self._sources: dict[str, Any] = {}
        self._fetch: FetchResult | None = None
        self._adapter_info: dict[str, str] = {}
        self.image_warnings: list[dict[str, str]] = []
        self._run_counts: dict[str, int] = {
            "fetched": 0,
            "considered": 0,
            "added": 0,
            "skipped_existing": 0,
            "skipped_duplicate": 0,
            "repair_attempted": 0,
            "repaired": 0,
            "failed": 0,
        }

    # ------------------------------------------------------------------
    # Public entry point

    def execute(self) -> CrawlResult:
        self.config.ensure_dirs()
        self._cleanup_old_staging()
        with FileLock(self.config.lock_path, "meme library"):
            self._recover_journals()
            return self._execute_locked()

    def _execute_locked(self) -> CrawlResult:
        self._catalog = load_catalog(self.config)
        self._manifest = load_manifest(self.config)
        self._state = load_state(self.config)
        self._sources = load_sources(self.config)
        if not self._catalog:
            leftover_ids = ids_from_image_dir(self.config.image_dir, self.config.id_max_digits)
            state_count = self._state.get("count")
            if leftover_ids or (isinstance(state_count, int) and state_count > 0):
                raise ValidationError(
                    "memes.json is missing or empty, but img/raw and/or state.json still "
                    "describe an existing library. Refusing to crawl because new ids would "
                    "duplicate the existing files. Restore data/backups/memes-*.json first "
                    "(then run verify), or explicitly remove the old images/state."
                )
        adapter_class = get_source(self.options.source)
        adapter = adapter_class()
        self._adapter_info = adapter.describe()
        logger.info(
            "source=%s (%s), existing catalog=%d, run=%s",
            adapter.key,
            adapter.display_name,
            len(self._catalog),
            self.run_id,
        )

        self._fetch = adapter.fetch(self.config)
        self.warnings.extend(self._fetch.warnings)
        items = list(self._fetch.items)
        self._run_counts["fetched"] = len(self._fetch.items)
        if self.options.limit is not None:
            if self.options.limit <= 0:
                raise ValidationError("--limit must be a positive integer")
            items = items[: self.options.limit]
            self.warnings.append(
                f"--limit={self.options.limit} applied; only the first {len(items)} items were considered"
            )
        if not items:
            raise ValidationError("source returned no items to consider")
        self._run_counts["considered"] = len(items)

        catalog_by_id, catalog_by_sha, catalog_by_source = catalog_index(self._catalog)
        manifest_by_source, manifest_by_url = index_manifest(self._manifest)
        catalog_by_url: dict[str, dict[str, Any]] = {}
        for entry in self._catalog:
            url = entry.get("source_url")
            if isinstance(url, str) and url:
                catalog_by_url[url] = entry

        existing: list[dict[str, Any]] = []
        repairs: list[_Pending] = []
        pending: list[_Pending] = []
        for index, item in enumerate(items):
            match = self._find_existing(
                item,
                catalog_by_id,
                catalog_by_source,
                catalog_by_url,
                manifest_by_source,
                manifest_by_url,
            )
            if match is None:
                pending.append(_Pending(index=index, item=item))
                continue
            entry = match
            file_path = self.config.root / entry["file"]
            if not file_path.exists():
                repairs.append(_Pending(index=index, item=item, existing_entry=entry))
                self.warnings.append(
                    f"id {entry['id']}: local file is missing; will try to restore it from the source"
                )
                continue
            existing.append(
                {
                    "id": entry["id"],
                    "source_id": item.source_item_id,
                    "name": item.name,
                    "reason": "already_present",
                }
            )
            expected_bytes = entry.get("bytes")
            if isinstance(expected_bytes, int):
                actual_bytes = file_path.stat().st_size
                if actual_bytes != expected_bytes:
                    self.warnings.append(
                        f"id {entry['id']}: local file is {actual_bytes} bytes, "
                        f"catalog says {expected_bytes}; run verify"
                    )
            if self.options.refresh_metadata:
                self._refresh_metadata(entry, item)
        self._run_counts["skipped_existing"] = len(existing)
        self._run_counts["repair_attempted"] = len(repairs)

        logger.info(
            "fetched=%d considered=%d existing=%d repair=%d new=%d",
            len(self._fetch.items),
            len(items),
            len(existing),
            len(repairs),
            len(pending),
        )

        if self.options.dry_run:
            summary = {
                "dry_run": True,
                "source": adapter.key,
                "run_id": self.run_id,
                "items_fetched": len(self._fetch.items),
                "items_considered": len(items),
                "items_new": len(pending),
                "items_existing": len(existing),
                "items_repair": len(repairs),
                "snapshot_written": False,
                "warnings": self.warnings,
            }
            self._print_dry_run(summary)
            return CrawlResult(exit_code=0, report=summary, added=0)

        self._save_snapshot()
        worker_count = self.options.workers or self.config.workers
        client = HttpClient(self.config, referer=adapter.referer or adapter.homepage)
        downloaded, failures = self._download_all(client, pending + repairs, worker_count)
        failures.extend(self._repair_failures(repairs, downloaded))

        # Deduplicate new content against the existing library and within this batch.
        kept, duplicates = self._dedupe(pending, downloaded, catalog_by_sha)
        for failure in failures:
            logger.error(
                "download failed for %s (%s): %s",
                failure.item.source_item_id,
                short(failure.item.name, 50),
                failure.error,
            )
        for downloaded_item in downloaded.values():
            for message in downloaded_item.warnings:
                self.image_warnings.append(
                    {
                        "source_id": downloaded_item.item.source_item_id,
                        "name": downloaded_item.item.name,
                        "warning": message,
                    }
                )

        existing_numbers = ids_from_catalog(self._catalog, self.config.id_max_digits)
        existing_numbers |= ids_from_image_dir(self.config.image_dir, self.config.id_max_digits)
        next_id = self._state.get("next_id")
        if not isinstance(next_id, int) or next_id < 1:
            next_id = max(existing_numbers, default=0) + 1
        allocated, next_after = allocate_ids(
            len(kept),
            existing_numbers=existing_numbers,
            next_id=next_id,
            min_width=self.config.id_min_width,
            max_digits=self.config.id_max_digits,
        )
        id_by_index = {pending_item.index: id_str for pending_item, id_str in zip(kept, allocated)}
        kept_by_index = {downloaded_item.index: downloaded_item for downloaded_item in kept}

        new_entries: list[dict[str, Any]] = []
        manifest_new: dict[str, dict[str, Any]] = {}
        added_at = iso_local()
        for downloaded_item, id_str in zip(kept, allocated):
            entry = self._make_entry(downloaded_item, id_str, added_at)
            manifest_item = self._make_manifest_item(downloaded_item, id_str, added_at)
            new_entries.append(entry)
            manifest_new[id_str] = manifest_item

        # Exact duplicates keep the first copy and preserve the other names as aliases.
        duplicate_aliases: dict[str, list[dict[str, Any]]] = {}
        for duplicate in duplicates:
            if duplicate.existing_id is not None:
                self._append_existing_alias(duplicate)
                continue
            canonical = kept_by_index.get(duplicate.kept_index)
            if canonical is None:
                continue
            canonical_id = id_by_index.get(canonical.index)
            if canonical_id is None:
                continue
            duplicate_aliases.setdefault(canonical_id, []).append(self._alias_for(duplicate))
        for id_str, aliases in duplicate_aliases.items():
            if id_str in manifest_new:
                manifest_new[id_str]["aliases"] = aliases

        # Metadata-only refreshes still need a catalog write.
        metadata_changed = bool(self.options.refresh_metadata and existing)
        repairs_ok = self._prepare_repairs(repairs, downloaded)
        failures = [failure for failure in failures if failure is not None]
        catalog_count_before = len(self._catalog)
        self._run_counts.update(
            {
                "added": len(new_entries),
                "skipped_duplicate": len(duplicates),
                "repaired": len(repairs_ok),
                "failed": len(failures),
            }
        )

        moves: list[dict[str, Any]] = []
        for entry, downloaded_item in repairs_ok:
            moves.append(
                {
                    "id": entry["id"],
                    "staging": relative_posix(downloaded_item.staging_path, self.config.root),
                    "final": entry["file"],
                    "sha256": entry["sha256"],
                }
            )
        for entry, downloaded_item in zip(new_entries, kept):
            moves.append(
                {
                    "id": entry["id"],
                    "staging": relative_posix(downloaded_item.staging_path, self.config.root),
                    "final": entry["file"],
                    "sha256": entry["sha256"],
                }
            )

        committed_catalog = self._commit(
            new_entries=new_entries,
            manifest_new=manifest_new,
            repairs_ok=repairs_ok,
            moves=moves,
            next_id=next_after,
            metadata_changed=metadata_changed,
        )

        verification = None
        if self.options.verify:
            from .verify import verify_library

            verification = verify_library(self.config, quiet=True, compute_dhash=False)
            if verification["errors"]:
                for error in verification["errors"][:20]:
                    logger.error("verify: %s", error)
                if len(verification["errors"]) > 20:
                    logger.error("verify: ... %d more errors", len(verification["errors"]) - 20)

        near_duplicates: list[dict[str, object]] = []
        near_duplicates_truncated = False
        if self.options.near_dup:
            near_duplicates, near_duplicates_truncated = self._near_duplicates(committed_catalog, new_entries)

        formats: dict[str, int] = {}
        for entry in new_entries:
            formats[entry["format"]] = formats.get(entry["format"], 0) + 1

        catalog_formats: dict[str, int] = {}
        catalog_bytes = 0
        for entry in committed_catalog:
            catalog_formats[entry["format"]] = catalog_formats.get(entry["format"], 0) + 1
            if isinstance(entry.get("bytes"), int):
                catalog_bytes += entry["bytes"]

        report: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "source": adapter.key,
            "source_display_name": adapter.display_name,
            "started_at": self.started_at.isoformat(),
            "finished_at": iso_local(),
            "duration_seconds": round(time.monotonic() - self.started_monotonic, 3),
            "dry_run": False,
            "items_fetched": len(self._fetch.items),
            "items_considered": len(items),
            "items_added": len(new_entries),
            "items_skipped_existing": len(existing),
            "items_skipped_duplicate": len(duplicates),
            "items_repaired": len(repairs_ok),
            "items_failed": len(failures),
            "bytes_added": sum(item.byte_size for item in kept),
            "formats_added": formats,
            "catalog_count_before": catalog_count_before,
            "catalog_count_after": len(committed_catalog),
            "catalog_bytes": catalog_bytes,
            "catalog_formats": catalog_formats,
            "next_id": self._state.get("next_id"),
            "snapshot": relative_posix(self.snapshot_path, self.config.root) if self.snapshot_path else None,
            "snapshot_sha256": self.snapshot_sha,
            "failures": [
                {
                    "source_id": failure.item.source_item_id,
                    "name": failure.item.name,
                    "image_url": failure.item.image_url,
                    "error": failure.error,
                }
                for failure in failures
            ],
            "duplicates": [
                {
                    "source_id": duplicate.item.source_item_id,
                    "name": duplicate.item.name,
                    "kept_id": duplicate.existing_id
                    or (id_by_index.get(duplicate.kept_index) if duplicate.kept_index >= 0 else None),
                    "reason": duplicate.key,
                }
                for duplicate in duplicates
            ],
            "near_duplicates": near_duplicates,
            "near_duplicates_truncated": near_duplicates_truncated,
            "image_warnings": self.image_warnings,
            "warnings": self.warnings,
            "verification": verification,
        }

        report_path = self.config.report_dir / f"crawl-{self.options.source}-{self.run_id}.json"
        report["report_path"] = relative_posix(report_path, self.config.root)
        atomic_write_json(report_path, report)
        logger.info(
            "added=%d duplicates=%d failed=%d catalog=%d next_id=%s",
            len(new_entries),
            len(duplicates),
            len(failures),
            len(committed_catalog),
            self._state.get("next_id"),
        )

        exit_code = 0
        if verification and verification["errors"]:
            exit_code = 3
        elif failures:
            exit_code = 2 if (new_entries or repairs_ok) else 1
        return CrawlResult(exit_code=exit_code, report=report, added=len(new_entries))

    # ------------------------------------------------------------------
    # Matching / metadata

    def _find_existing(
        self,
        item: SourceItem,
        catalog_by_id: dict[str, dict[str, Any]],
        catalog_by_source: dict[tuple[str, str], dict[str, Any]],
        catalog_by_url: dict[str, dict[str, Any]],
        manifest_by_source: dict[tuple[str, str], str],
        manifest_by_url: dict[str, str],
    ) -> dict[str, Any] | None:
        source_key = (self.options.source, item.source_item_id)
        existing_id = manifest_by_source.get(source_key)
        if existing_id is None:
            entry = catalog_by_source.get(source_key)
            if entry is not None:
                existing_id = entry["id"]
        if existing_id is not None:
            entry = catalog_by_id.get(existing_id)
            if entry is not None:
                return entry
            self.warnings.append(
                f"manifest refers to missing catalog id {existing_id!r} for source item "
                f"{item.source_item_id}; treating it as new"
            )
        if item.image_url in catalog_by_url:
            return catalog_by_url[item.image_url]
        existing_id = manifest_by_url.get(item.image_url)
        if existing_id is not None:
            entry = catalog_by_id.get(existing_id)
            if entry is not None:
                return entry
        return None

    def _refresh_metadata(self, entry: dict[str, Any], item: SourceItem) -> None:
        name = clean_name(item.name, fallback=entry["name"])
        changed = False
        if name and name != entry["name"]:
            entry["name"] = name
            changed = True
        manifest_item = self._manifest.get("items", {}).get(entry["id"])
        if manifest_item is not None:
            for key, value in (
                ("name", name),
                ("description", item.description),
                ("category", item.category),
                ("kind", item.kind),
                ("source_label", item.source_label),
                ("source_page_url", item.source_page_url),
            ):
                if value and manifest_item.get(key) != value:
                    manifest_item[key] = value
                    changed = True
        if changed:
            logger.info("refreshed metadata for id %s", entry["id"])

    # ------------------------------------------------------------------
    # Snapshot

    def _save_snapshot(self) -> None:
        if self._fetch is None or self._fetch.snapshot_bytes is None:
            return
        data = self._fetch.snapshot_bytes
        digest = sha256_bytes(data)
        site_dir = self.config.snapshot_dir / self.options.source
        site_dir.mkdir(parents=True, exist_ok=True)
        suffix = self._fetch.snapshot_suffix or ".json"
        path = site_dir / f"{self.run_id}-{digest[:8]}{suffix}"
        atomic_write_bytes(path, data)
        self.snapshot_path = path
        self.snapshot_sha = digest
        logger.info("saved source snapshot: %s (%s)", relative_posix(path, self.config.root), human_bytes(len(data)))

    # ------------------------------------------------------------------
    # Downloads

    def _download_all(
        self,
        client: HttpClient,
        pending: list[_Pending],
        workers: int,
    ) -> tuple[dict[int, Downloaded], list[DownloadFailure]]:
        if not pending:
            return {}, []
        self.staging_run.mkdir(parents=True, exist_ok=True)
        downloaded: dict[int, Downloaded] = {}
        failures: list[DownloadFailure] = []
        total = len(pending)
        logger.info("downloading %d image(s) with %d worker(s)", total, workers)
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="meme-download"
        ) as executor:
            futures = {
                executor.submit(self._download_one, client, pending_item): pending_item
                for pending_item in pending
            }
            completed = 0
            try:
                for future in concurrent.futures.as_completed(futures):
                    completed += 1
                    pending_item = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001 - keep the batch running
                        result = DownloadFailure(
                            index=pending_item.index,
                            item=pending_item.item,
                            error=f"worker crashed: {type(exc).__name__}: {exc}",
                        )
                    if isinstance(result, Downloaded):
                        downloaded[result.index] = result
                    else:
                        failures.append(result)
                    if completed % 25 == 0 or completed == total:
                        logger.info(
                            "download progress: %d/%d (ok=%d failed=%d)",
                            completed,
                            total,
                            len(downloaded),
                            len(failures),
                        )
            except KeyboardInterrupt:
                logger.warning("interrupted; cancelling remaining downloads")
                for future in futures:
                    future.cancel()
                raise
        return downloaded, failures

    def _download_one(self, client: HttpClient, pending: _Pending) -> Downloaded | DownloadFailure:
        item = pending.item
        staging_path = self.staging_run / f"{pending.index:06d}.download"
        try:
            validate_http_url(item.image_url)
            result = client.download(item.image_url, staging_path)
            if not staging_path.exists():
                raise PipelineError("download produced no file")
            actual_sha = sha256_file(staging_path)
            if actual_sha != result.sha256:
                raise PipelineError(
                    f"hash mismatch after write: server stream {result.sha256[:12]}…, "
                    f"disk {actual_sha[:12]}…"
                )
            actual_size = staging_path.stat().st_size
            if actual_size != result.bytes_written:
                raise PipelineError(
                    f"size mismatch after write: {result.bytes_written} vs {actual_size} bytes"
                )
            info = inspect_image(staging_path)
            image_warnings = list(info.warnings)
            content_type = (result.content_type or "").split(";")[0].strip().lower()
            if content_type and not content_type.startswith("image/"):
                image_warnings.append(
                    f"server sent non-image Content-Type {result.content_type!r}; trusted file signature instead"
                )
            elif content_type:
                expected_mime = {
                    "jpeg": {"image/jpeg", "image/jpg"},
                    "png": {"image/png"},
                    "gif": {"image/gif"},
                    "webp": {"image/webp"},
                    "avif": {"image/avif"},
                    "heif": {"image/heic", "image/heif"},
                }.get(info.format, set())
                if expected_mime and content_type not in expected_mime:
                    image_warnings.append(
                        f"Content-Type {result.content_type!r} disagrees with actual {info.format}"
                    )
            url_ext = Path(urlparse(item.image_url).path).suffix.lower().lstrip(".")
            if url_ext in {"jpeg", "jpe", "jfif"}:
                url_ext = "jpg"
            if url_ext and url_ext != info.ext:
                image_warnings.append(
                    f"URL suffix .{url_ext} disagrees with actual {info.format}; used .{info.ext}"
                )
            for message in image_warnings:
                logger.warning(
                    "id-pending %s (%s): %s",
                    item.source_item_id,
                    short(item.name, 40),
                    message,
                )
            return Downloaded(
                item=item,
                index=pending.index,
                staging_path=staging_path,
                sha256=actual_sha,
                format=info.format,
                ext=info.ext,
                width=info.width,
                height=info.height,
                frames=info.frames,
                byte_size=actual_size,
                content_type=result.content_type,
                final_url=result.final_url,
                dhash=info.dhash,
                warnings=image_warnings,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in the report
            staging_path.unlink(missing_ok=True)
            return DownloadFailure(
                index=pending.index,
                item=item,
                error=f"{type(exc).__name__}: {exc}",
            )

    # ------------------------------------------------------------------
    # Deduplication / repair bookkeeping

    def _dedupe(
        self,
        pending: list[_Pending],
        downloaded: dict[int, Downloaded],
        catalog_by_sha: dict[str, dict[str, Any]],
    ) -> tuple[list[Downloaded], list[DuplicateRecord]]:
        pending_indexes = {pending_item.index for pending_item in pending}
        kept: list[Downloaded] = []
        duplicates: list[DuplicateRecord] = []
        seen: dict[str, Downloaded] = {}
        for index in sorted(pending_indexes):
            downloaded_item = downloaded.get(index)
            if downloaded_item is None:
                continue
            digest = downloaded_item.sha256
            existing = catalog_by_sha.get(digest)
            if existing is not None:
                duplicates.append(
                    DuplicateRecord(
                        key="same_sha256_as_existing",
                        kept_index=-1,
                        duplicate_index=index,
                        item=downloaded_item.item,
                        existing_id=existing["id"],
                    )
                )
                downloaded_item.staging_path.unlink(missing_ok=True)
                continue
            canonical = seen.get(digest)
            if canonical is not None:
                duplicates.append(
                    DuplicateRecord(
                        key="same_sha256_in_batch",
                        kept_index=canonical.index,
                        duplicate_index=index,
                        item=downloaded_item.item,
                    )
                )
                downloaded_item.staging_path.unlink(missing_ok=True)
                continue
            seen[digest] = downloaded_item
            kept.append(downloaded_item)
        return kept, duplicates

    def _repair_failures(
        self,
        repairs: list[_Pending],
        downloaded: dict[int, Downloaded],
    ) -> list[DownloadFailure]:
        failures: list[DownloadFailure] = []
        for pending_item in repairs:
            downloaded_item = downloaded.get(pending_item.index)
            if downloaded_item is None:
                continue
            expected = pending_item.existing_entry
            if expected is None:
                continue
            if downloaded_item.sha256 != expected["sha256"]:
                downloaded_item.staging_path.unlink(missing_ok=True)
                del downloaded[pending_item.index]
                failures.append(
                    DownloadFailure(
                        index=pending_item.index,
                        item=pending_item.item,
                        error=(
                            "source content changed: the local file is missing and the new "
                            "download does not match the catalog sha256; refusing to overwrite "
                            "the stable id"
                        ),
                    )
                )
        return failures

    def _prepare_repairs(
        self,
        repairs: list[_Pending],
        downloaded: dict[int, Downloaded],
    ) -> list[tuple[dict[str, Any], Downloaded]]:
        result: list[tuple[dict[str, Any], Downloaded]] = []
        for pending_item in repairs:
            downloaded_item = downloaded.get(pending_item.index)
            if downloaded_item is None or pending_item.existing_entry is None:
                continue
            result.append((pending_item.existing_entry, downloaded_item))
        return result

    def _alias_for(self, duplicate: DuplicateRecord) -> dict[str, Any]:
        return {
            "source": self.options.source,
            "source_id": duplicate.item.source_item_id,
            "name": duplicate.item.name,
            "image_url": duplicate.item.image_url,
            "page_url": duplicate.item.page_url,
            "seen_at": iso_local(),
            "reason": duplicate.key,
        }

    def _append_existing_alias(self, duplicate: DuplicateRecord) -> None:
        if duplicate.existing_id is None:
            return
        items = self._manifest.setdefault("items", {})
        item = items.get(duplicate.existing_id)
        if not isinstance(item, dict):
            return
        aliases = item.setdefault("aliases", [])
        if not isinstance(aliases, list):
            aliases = []
            item["aliases"] = aliases
        alias = self._alias_for(duplicate)
        if any(
            existing.get("source") == alias["source"] and existing.get("source_id") == alias["source_id"]
            for existing in aliases
            if isinstance(existing, dict)
        ):
            return
        aliases.append(alias)

    # ------------------------------------------------------------------
    # Entry construction

    def _make_entry(self, downloaded_item: Downloaded, id_str: str, added_at: str) -> dict[str, Any]:
        image_prefix = self.config.rel(self.config.image_dir)
        item = downloaded_item.item
        entry: dict[str, Any] = {
            "id": id_str,
            "name": clean_name(item.name, fallback=f"未命名表情包-{item.source_item_id[:8]}"),
            "tag": [],
            "file": f"{image_prefix}/{id_str}.{downloaded_item.ext}",
            "format": downloaded_item.format,
            "width": downloaded_item.width,
            "height": downloaded_item.height,
            "bytes": downloaded_item.byte_size,
            "sha256": downloaded_item.sha256,
            "source": self.options.source,
            "source_id": item.source_item_id,
            "source_url": item.image_url,
            "source_page": item.page_url,
            "added_at": added_at,
        }
        if not entry["source_page"]:
            entry.pop("source_page")
        return compact_catalog_entry(entry)

    def _make_manifest_item(
        self,
        downloaded_item: Downloaded,
        id_str: str,
        added_at: str,
    ) -> dict[str, Any]:
        item = downloaded_item.item
        extra = item.extra or {}
        manifest_item: dict[str, Any] = {
            "source": self.options.source,
            "source_id": item.source_item_id,
            "name": item.name,
            "description": item.description,
            "category": item.category,
            "kind": item.kind,
            "source_label": item.source_label,
            "source_page_url": item.source_page_url,
            "source_tags": extra.get("source_tags") or [],
            "image_url": item.image_url,
            "page_url": item.page_url,
            "final_url": downloaded_item.final_url,
            "content_type": downloaded_item.content_type,
            "format": downloaded_item.format,
            "width": downloaded_item.width,
            "height": downloaded_item.height,
            "frames": downloaded_item.frames,
            "bytes": downloaded_item.byte_size,
            "sha256": downloaded_item.sha256,
            "dhash": downloaded_item.dhash,
            "downloaded_at": iso_local(),
            "added_at": added_at,
            "aliases": [],
        }
        if isinstance(item.published_at, dt.datetime):
            manifest_item["published_at"] = item.published_at.isoformat()
        elif isinstance(item.published_at, str) and item.published_at.strip():
            manifest_item["published_at"] = item.published_at
        for key in ("views", "downloads", "featured", "created_at", "source_index"):
            value = extra.get(key)
            if value is not None:
                manifest_item[key] = value
        return manifest_item

    # ------------------------------------------------------------------
    # Commit

    def _commit(
        self,
        *,
        new_entries: list[dict[str, Any]],
        manifest_new: dict[str, dict[str, Any]],
        repairs_ok: list[tuple[dict[str, Any], Downloaded]],
        moves: list[dict[str, Any]],
        next_id: int,
        metadata_changed: bool,
    ) -> list[dict[str, Any]]:
        catalog = list(self._catalog) + list(new_entries)
        catalog = [compact_catalog_entry(entry) for entry in catalog]
        validate_catalog(catalog, self.config)

        # Always create a journal: even a metadata-only commit must be able to
        # reconcile the manifest if the process dies between the two writes.
        self._write_journal(moves)
        try:
            self._apply_moves(moves)
            self._commit_metadata(
                catalog=catalog,
                manifest_new=manifest_new,
                repairs_ok=repairs_ok,
                next_id=next_id,
            )
        except BaseException:
            logger.exception("commit failed; the journal will be used to roll back")
            with contextlib.suppress(Exception):
                self._recover_journals()
            raise
        finally:
            # If recovery could not finish, keep the staging files for the next
            # start; deleting them would destroy the only recoverable copy.
            if not self.options.keep_staging and not self.journal_path.exists():
                shutil.rmtree(self.staging_run, ignore_errors=True)
        self._catalog = catalog
        return catalog

    def _write_journal(self, moves: list[dict[str, Any]]) -> None:
        payload = {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "status": "prepared",
            "created_at": iso_local(),
            "moves": moves,
        }
        atomic_write_json(self.journal_path, payload)

    def _apply_moves(self, moves: list[dict[str, Any]]) -> None:
        for move in moves:
            staging = self.config.root / move["staging"]
            final = self.config.root / move["final"]
            expected = move["sha256"]
            if not staging.exists():
                if final.exists() and sha256_file(final) == expected:
                    continue
                raise PipelineError(f"staging file disappeared: {staging}")
            if sha256_file(staging) != expected:
                raise PipelineError(f"staging file hash changed: {staging}")
            if final.exists():
                if sha256_file(final) == expected:
                    staging.unlink(missing_ok=True)
                    continue
                raise PipelineError(
                    f"refusing to overwrite a different file at {final}; "
                    "run `python3 -m meme_pipeline verify` and resolve the conflict"
                )
            final.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging, final)
        logger.info("moved %d file(s) into place", len(moves))

    def _commit_metadata(
        self,
        *,
        catalog: list[dict[str, Any]],
        manifest_new: dict[str, dict[str, Any]],
        repairs_ok: list[tuple[dict[str, Any], Downloaded]],
        next_id: int,
    ) -> None:
        catalog_ids = {entry["id"] for entry in catalog}
        items = self._manifest.setdefault("items", {})
        if not isinstance(items, dict):
            items = {}
            self._manifest["items"] = items
        # Drop manifest entries whose catalog entry was removed manually.
        for stale_id in [key for key in items if key not in catalog_ids]:
            del items[stale_id]
        # Backfill provenance for catalog entries that predate the manifest.
        for entry in catalog:
            if entry["id"] not in items:
                items[entry["id"]] = {
                    "source": entry["source"],
                    "source_id": entry.get("source_id", ""),
                    "name": entry["name"],
                    "image_url": entry["source_url"],
                    "page_url": entry.get("source_page", ""),
                    "format": entry["format"],
                    "width": entry.get("width"),
                    "height": entry.get("height"),
                    "bytes": entry.get("bytes"),
                    "sha256": entry["sha256"],
                    "aliases": [],
                }
        items.update(manifest_new)
        for entry, downloaded_item in repairs_ok:
            item = items.get(entry["id"])
            if isinstance(item, dict):
                item["restored_at"] = iso_local()
                item["restored_sha256"] = downloaded_item.sha256

        save_manifest(self.config, self._manifest, self.run_id)
        catalog_sha = save_catalog(self.config, catalog, self.run_id)
        self._catalog = catalog
        self._state = {
            "schema_version": SCHEMA_VERSION,
            "next_id": max(next_id, max([int(entry["id"], 10) for entry in catalog], default=0) + 1),
            "count": len(catalog),
            "catalog_sha256": catalog_sha,
            "last_run_id": self.run_id,
            "updated_at": iso_local(),
        }
        save_state(self.config, self._state)
        self._update_sources_registry(len(catalog))
        self.journal_path.unlink(missing_ok=True)

    def _update_sources_registry(self, catalog_count: int) -> None:
        sources = self._sources.setdefault("sources", {})
        prior = sources.get(self.options.source, {})
        if not isinstance(prior, dict):
            prior = {}
        now = iso_local()
        counts = self._run_counts
        added = counts.get("added", 0)
        entry = {
            "display_name": self._adapter_info.get("display_name", self.options.source),
            "homepage": self._adapter_info.get("homepage", ""),
            "adapter_version": self._adapter_info.get("version", "1"),
            "first_crawled_at": prior.get("first_crawled_at", now),
            "last_crawled_at": now,
            "last_run_id": self.run_id,
            "last_snapshot": (
                relative_posix(self.snapshot_path, self.config.root) if self.snapshot_path else None
            ),
            "last_snapshot_sha256": self.snapshot_sha,
            "items_fetched": counts.get("fetched", 0),
            "items_considered": counts.get("considered", 0),
            "items_added": added,
            "items_skipped_existing": counts.get("skipped_existing", 0),
            "items_skipped_duplicate": counts.get("skipped_duplicate", 0),
            "items_repair_attempted": counts.get("repair_attempted", 0),
            "items_repaired": counts.get("repaired", 0),
            "items_failed": counts.get("failed", 0),
            "catalog_count_after": catalog_count,
            "total_added_all_time": int(prior.get("total_added_all_time", 0)) + added,
            "runs": int(prior.get("runs", 0)) + 1,
        }
        sources[self.options.source] = entry
        save_sources(self.config, self._sources, self.run_id)

    # ------------------------------------------------------------------
    # Near-duplicate report

    def _near_duplicates(
        self,
        catalog: list[dict[str, Any]],
        new_entries: list[dict[str, Any]],
    ) -> tuple[list[dict[str, object]], bool]:
        items = self._manifest.get("items", {})
        new_ids = {entry["id"] for entry in new_entries}
        if not new_ids:
            return [], False
        hashed: list[tuple[str, str]] = []
        for entry in catalog:
            metadata = items.get(entry["id"], {})
            dhash = metadata.get("dhash") if isinstance(metadata, dict) else None
            if isinstance(dhash, str) and len(dhash) == 16:
                hashed.append((entry["id"], dhash))
        pairs, truncated = find_near_duplicate_pairs(
            hashed,
            threshold=self.config.near_dup_hamming,
            limit=self.config.near_dup_max_pairs,
        )
        # Only report pairs involving something added in this run.
        filtered = [
            pair
            for pair in pairs
            if str(pair["a"]) in new_ids or str(pair["b"]) in new_ids
        ]
        return filtered, truncated

    # ------------------------------------------------------------------
    # Recovery / cleanup / reporting

    def _recover_journals(self) -> None:
        if not self.config.journal_dir.exists():
            return
        for journal_path in sorted(self.config.journal_dir.glob("*.json")):
            try:
                journal = json.loads(journal_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                logger.error("cannot read journal %s: %s", journal_path, exc)
                continue
            moves = journal.get("moves")
            if not isinstance(moves, list):
                logger.error("journal %s has no moves list; leaving it untouched", journal_path)
                continue
            catalog = load_catalog(self.config)
            catalog_ids = {entry["id"] for entry in catalog}
            had_error = False
            for move in moves:
                if not isinstance(move, dict):
                    continue
                move_id = move.get("id")
                staging = self.config.root / str(move.get("staging", ""))
                final = self.config.root / str(move.get("final", ""))
                expected = move.get("sha256")
                if not isinstance(move_id, str) or not isinstance(expected, str):
                    had_error = True
                    logger.error("journal %s: malformed move %r", journal_path, move)
                    continue
                try:
                    if move_id in catalog_ids:
                        # Committed (or a repair of an existing id): keep the final file.
                        if final.exists():
                            if sha256_file(final) != expected:
                                had_error = True
                                logger.error(
                                    "journal %s: id %s is committed but %s has a different hash",
                                    journal_path,
                                    move_id,
                                    final,
                                )
                            else:
                                staging.unlink(missing_ok=True)
                        elif staging.exists() and sha256_file(staging) == expected:
                            final.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(staging, final)
                            logger.warning("recovered interrupted commit: restored %s", final)
                        else:
                            had_error = True
                            logger.error(
                                "journal %s: id %s is committed but %s is missing",
                                journal_path,
                                move_id,
                                final,
                            )
                    else:
                        # Not committed: remove the orphaned final file, then staging.
                        if final.exists():
                            if sha256_file(final) == expected:
                                final.unlink()
                                logger.warning(
                                    "removed orphaned file from interrupted commit: %s", final
                                )
                            else:
                                had_error = True
                                logger.error(
                                    "journal %s: refusing to delete %s because its hash changed",
                                    journal_path,
                                    final,
                                )
                        staging.unlink(missing_ok=True)
                except Exception as exc:  # noqa: BLE001 - keep recovery going
                    had_error = True
                    logger.error(
                        "journal %s: could not recover id %s: %s",
                        journal_path,
                        move_id,
                        exc,
                    )
            if had_error:
                logger.error(
                    "journal %s could not be fully recovered; leaving it in place for inspection",
                    journal_path,
                )
                continue
            # Manifest is derived from the catalog; prune entries the catalog lacks.
            try:
                manifest = load_manifest(self.config)
                items = manifest.get("items", {})
                changed = False
                for key in [key for key in items if key not in catalog_ids]:
                    del items[key]
                    changed = True
                if changed:
                    save_manifest(self.config, manifest, self.run_id)
                    logger.warning("pruned manifest entries that were not committed")
            except Exception as exc:  # noqa: BLE001 - recovery must be best effort
                logger.error("could not reconcile manifest after journal recovery: %s", exc)
            journal_path.unlink(missing_ok=True)
            logger.warning("recovered and removed journal %s", journal_path)

    def _cleanup_old_staging(self) -> None:
        if not self.config.staging_dir.exists():
            return
        cutoff = time.time() - 7 * 24 * 3600
        for path in self.config.staging_dir.iterdir():
            if not path.is_dir():
                continue
            with contextlib.suppress(OSError):
                if path.stat().st_mtime < cutoff:
                    shutil.rmtree(path, ignore_errors=True)

    def _write_report(self, report: dict[str, Any]) -> Path:
        self.config.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.report_dir / f"crawl-{self.options.source}-{self.run_id}.json"
        atomic_write_json(path, report)
        return path

    def _print_dry_run(self, summary: dict[str, Any]) -> None:
        print("DRY RUN — nothing was downloaded or written to the catalog")
        print(f"  source:      {summary['source']}")
        print(f"  fetched:     {summary['items_fetched']}")
        print(f"  considered:  {summary['items_considered']}")
        print(f"  new:         {summary['items_new']}")
        print(f"  existing:    {summary['items_existing']}")
        print(f"  repair:      {summary['items_repair']}")
        for warning in summary.get("warnings", [])[:10]:
            print(f"  warning:     {warning}")
        if len(summary.get("warnings", [])) > 10:
            print(f"  warning:     ... {len(summary['warnings']) - 10} more")
