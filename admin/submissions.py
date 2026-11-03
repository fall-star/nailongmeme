"""Quarantined public-submission inbox used by the local admin app."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from admin import ingest
from meme_pipeline.catalog import load_catalog
from meme_pipeline.config import Config
from meme_pipeline.imaging import inspect_image
from meme_pipeline.util import ValidationError, atomic_write_json, iso_local, sha256_file

STATUSES = {"received", "pending_review", "approved", "rejected", "duplicate", "needs_info"}


def submissions_dir(cfg: Config) -> Path:
    path = cfg.root / "data" / "submissions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(cfg: Config, submission_id: str) -> Path:
    return submissions_dir(cfg) / submission_id / "submission.json"


def _load(cfg: Config, submission_id: str) -> dict[str, Any]:
    path = _record_path(cfg, submission_id)
    if not path.is_file():
        raise ValidationError(f"submission not found: {submission_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValidationError("invalid submission record")
    return data


def create_submission(
    body: bytes,
    *,
    filename: str,
    cfg: Config,
    name: str = "",
    tags: list[str] | None = None,
    source: str = "",
    contact: str = "",
    rights_accepted: bool = False,
) -> dict[str, Any]:
    if not rights_accepted:
        raise ValidationError("必须确认拥有图片投稿和展示权限")
    if not body:
        raise ValidationError("empty submission")
    if len(body) > ingest.MAX_BYTES:
        raise ValidationError(f"图片不能超过 {ingest.MAX_BYTES // 1024 // 1024} MB")
    safe_name = Path(filename).name or "submission.bin"
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ingest.IMAGE_EXTENSIONS:
        raise ValidationError("不支持的图片格式")

    submission_id = uuid.uuid4().hex
    folder = submissions_dir(cfg) / submission_id
    folder.mkdir(parents=True, exist_ok=True)
    temp = folder / f"original{suffix}"
    temp.write_bytes(body)

    try:
        meta = inspect_image(temp)
        digest = sha256_file(temp)
        by_sha = {str(entry.get("sha256")): str(entry.get("id")) for entry in load_catalog(cfg)}
        duplicate_id = by_sha.get(digest)
        status = "duplicate" if duplicate_id else "pending_review"
        record = {
            "submission_id": submission_id,
            "status": status,
            "name": name.strip()[:120],
            "tag": [str(tag).strip() for tag in (tags or []) if str(tag).strip()],
            "source": source.strip()[:500],
            "contact": contact.strip()[:300],
            "rights_accepted": True,
            "original": temp.relative_to(folder).as_posix(),
            "format": meta.format,
            "width": meta.width,
            "height": meta.height,
            "frames": meta.frames,
            "bytes": len(body),
            "sha256": digest,
            "duplicate_id": duplicate_id,
            "catalog_id": None,
            "created_at": iso_local(),
            "reviewed_at": None,
            "review_note": "",
        }
        atomic_write_json(folder / "submission.json", record)
        return _public_record(record)
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _public_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "submission_id": record["submission_id"],
        "status": record["status"],
        "created_at": record.get("created_at"),
    }


def list_submissions(cfg: Config, status: str = "") -> list[dict[str, Any]]:
    root = submissions_dir(cfg)
    records: list[dict[str, Any]] = []
    for path in sorted(root.glob("*/submission.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if status and record.get("status") != status:
            continue
        folder = path.parent
        record["preview_url"] = f"/api/admin/submissions/{record['submission_id']}/preview"
        record["folder"] = folder.name
        records.append(record)
    return records


def preview_path(cfg: Config, submission_id: str) -> Path:
    record = _load(cfg, submission_id)
    folder = submissions_dir(cfg) / submission_id
    path = folder / record.get("original", "")
    if not path.is_file():
        raise ValidationError("submission image is missing")
    return path


def approve_submission(
    cfg: Config,
    submission_id: str,
    *,
    name: str,
    tags: list[str],
    publish: bool = True,
) -> dict[str, Any]:
    record = _load(cfg, submission_id)
    if record.get("status") == "approved":
        raise ValidationError("submission already approved")
    if record.get("status") == "rejected":
        raise ValidationError("submission already rejected")
    original = submissions_dir(cfg) / submission_id / record["original"]
    if not original.is_file():
        raise ValidationError("submission image is missing")
    scan = ingest.scan_single(original, root=cfg.root)
    if not scan.items or scan.items[0].status == "duplicate":
        duplicate = scan.items[0].duplicate_id if scan.items else None
        record.update(
            {
                "status": "duplicate",
                "duplicate_id": duplicate,
                "reviewed_at": iso_local(),
                "review_note": "审核时发现已经入库",
            }
        )
        atomic_write_json(_record_path(cfg, submission_id), record)
        return {"submission_id": submission_id, "status": "duplicate", "catalog_id": None, "duplicate_id": duplicate}
    result = ingest.commit_scan(
        scan,
        [{"token": scan.items[0].token, "name": name, "tag": tags}],
        root=cfg.root,
        source_type="submission",
        submission_id=submission_id,
        status="published" if publish else "draft",
    )
    if not result["imported"]:
        raise ValidationError(f"submission import failed: {result['failed']}")
    catalog_id = result["imported"][0]["id"]
    record.update(
        {
            "status": "approved",
            "name": name[:120],
            "tag": [str(tag).strip() for tag in tags if str(tag).strip()],
            "catalog_id": catalog_id,
            "reviewed_at": iso_local(),
            "review_note": "",
        }
    )
    atomic_write_json(_record_path(cfg, submission_id), record)
    return {"submission_id": submission_id, "status": "approved", "catalog_id": catalog_id, "duplicate_id": None}


def reject_submission(cfg: Config, submission_id: str, reason: str = "") -> dict[str, Any]:
    record = _load(cfg, submission_id)
    record.update(
        {
            "status": "rejected",
            "reviewed_at": iso_local(),
            "review_note": reason.strip()[:500],
        }
    )
    atomic_write_json(_record_path(cfg, submission_id), record)
    return {"submission_id": submission_id, "status": "rejected"}
