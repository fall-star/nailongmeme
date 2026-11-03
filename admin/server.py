"""Local-only admin API and UI.

Run with:
    ./run.sh admin

Only binds to 127.0.0.1 and is never part of the production API or frontend
build.  The public API remains read-only.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from admin import ingest, submissions
from meme_pipeline.config import Config
from meme_pipeline.util import ValidationError

ROOT = Path(__file__).resolve().parents[1]
UI_DIR = Path(__file__).resolve().parent / "ui"
IMAGE_ID_RE = re.compile(r"^[0-9]{1,5}$")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(
    title="Nailong Meme Local Admin",
    docs_url="/api/admin/docs",
    redoc_url=None,
    openapi_url="/api/admin/openapi.json",
)

# Scans live only in this local process.  They hold absolute source paths,
# which is fine for a single-operator local tool and never serialized to the UI.
SCANS: dict[str, ingest.Scan] = {}


def cfg() -> Config:
    return ingest.get_config(os.getenv("MEME_DATA_DIR") or ROOT)


def _jobs_dir() -> Path:
    path = cfg().root / "data" / "admin" / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_file(scan_id: str) -> Path:
    return _jobs_dir() / f"{scan_id}.json"


def _save_job(scan: ingest.Scan) -> None:
    payload = {
        "scan_id": scan.scan_id,
        "source_dir": str(scan.source_dir) if scan.source_dir else None,
        "created_at": scan.created_at,
        "items": [
            {
                "token": item.token,
                "source_path": str(item.source_path),
                "relative_path": item.relative_path,
                "display_name": item.display_name,
                "status": item.status,
                "reason": item.reason,
                "duplicate_id": item.duplicate_id,
                "image_format": item.image_format,
                "extension": item.extension,
                "width": item.width,
                "height": item.height,
                "frames": item.frames,
                "bytes": item.bytes,
                "sha256": item.sha256,
                "dhash": item.dhash,
                "warnings": item.warnings,
            }
            for item in scan.items
        ],
    }
    target = _job_file(scan.scan_id)
    target.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_job(scan_id: str) -> ingest.Scan | None:
    path = _job_file(scan_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = [
            ingest.ScanItem(
                token=item["token"],
                source_path=Path(item["source_path"]),
                relative_path=item.get("relative_path", ""),
                display_name=item.get("display_name", ""),
                status=item.get("status", "invalid"),
                reason=item.get("reason", ""),
                duplicate_id=item.get("duplicate_id"),
                image_format=item.get("image_format"),
                extension=item.get("extension"),
                width=item.get("width"),
                height=item.get("height"),
                frames=item.get("frames", 1),
                bytes=item.get("bytes", 0),
                sha256=item.get("sha256", ""),
                dhash=item.get("dhash"),
                warnings=item.get("warnings", []),
            )
            for item in data.get("items", [])
        ]
        source_dir = Path(data["source_dir"]) if data.get("source_dir") else None
        return ingest.Scan(
            scan_id=data["scan_id"],
            source_dir=source_dir,
            items=items,
            created_at=data.get("created_at", ""),
        )
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _load_jobs() -> None:
    for path in sorted(_jobs_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
        scan = _load_job(path.stem)
        if scan is not None:
            SCANS[scan.scan_id] = scan
    _trim_scans()


def _delete_job(scan_id: str) -> None:
    _job_file(scan_id).unlink(missing_ok=True)
    SCANS.pop(scan_id, None)


def _trim_scans() -> None:
    jobs = sorted(_jobs_dir().glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for old in jobs[20:]:
        old.unlink(missing_ok=True)
    while len(SCANS) > 20:
        oldest = next(iter(SCANS))
        SCANS.pop(oldest, None)


class LocalOnlyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        host = request.client.host if request.client else ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            return JSONResponse({"detail": "admin is local-only"}, status_code=403)
        return await call_next(request)


app.add_middleware(LocalOnlyMiddleware)


@app.on_event("startup")
def _startup() -> None:
    _load_jobs()


class ImageEdit(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    tag: list[str] | None = None
    status: Literal["published", "draft", "blocked"] | None = None


class ScanRequest(BaseModel):
    path: str
    recursive: bool = True


class CommitItem(BaseModel):
    token: str
    name: str | None = None
    tag: list[str] = Field(default_factory=list)


class CommitRequest(BaseModel):
    items: list[CommitItem]


class SubmissionApprove(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    tag: list[str] = Field(default_factory=list)
    publish: bool = True


class SubmissionReject(BaseModel):
    reason: str = Field(default="", max_length=500)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/admin")


@app.get("/admin", include_in_schema=False)
@app.get("/admin/", include_in_schema=False)
def admin_page() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


app.mount("/admin/static", StaticFiles(directory=str(UI_DIR)), name="admin-static")


@app.get("/api/admin/stats")
def stats() -> dict[str, Any]:
    return ingest.stats(cfg())


@app.get("/api/admin/tags")
def tags() -> dict[str, Any]:
    return ingest.list_tags(cfg())


@app.get("/api/admin/images")
def images(
    q: str = "",
    tag: str = "",
    status: str = "",
    missing: str = "",
    limit: int = Query(60, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return ingest.list_images(cfg(), q=q, tag=tag, status=status, missing=missing, limit=limit, offset=offset)


@app.get("/api/admin/images/{image_id}")
def image(image_id: str) -> dict[str, Any]:
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise HTTPException(404, "image not found")
    entry = ingest.entry_for_media(cfg(), image_id)
    if entry is None:
        raise HTTPException(404, "image not found")
    return ingest._image_view(entry)  # noqa: SLF001 - shared local view helper


@app.patch("/api/admin/images/{image_id}")
def edit_image(image_id: str, body: ImageEdit) -> dict[str, Any]:
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise HTTPException(404, "image not found")
    try:
        entry = ingest.edit_image(image_id, name=body.name, tags=body.tag, status=body.status, root=cfg().root)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ingest._image_view(entry)  # noqa: SLF001


@app.get("/api/admin/media/{image_id}")
def media(image_id: str) -> FileResponse:
    if not IMAGE_ID_RE.fullmatch(image_id):
        raise HTTPException(404, "image not found")
    path = ingest.raw_or_converted(cfg(), image_id)
    if path is None or not path.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(path)


@app.get("/api/admin/media/{image_id}/thumb/{size}")
def media_thumb(image_id: str, size: int) -> FileResponse:
    if not IMAGE_ID_RE.fullmatch(image_id) or size not in (320, 640):
        raise HTTPException(404, "thumbnail not found")
    path = cfg().root / "img" / "thumbs" / str(size) / f"{image_id}.webp"
    if not path.is_file():
        path = ingest.raw_or_converted(cfg(), image_id) or Path("/__missing__")
    if not path.is_file():
        raise HTTPException(404, "thumbnail not found")
    return FileResponse(path)


@app.post("/api/admin/scan")
def scan(body: ScanRequest) -> dict[str, Any]:
    try:
        result = ingest.scan_folder(body.path, root=cfg().root, recursive=body.recursive)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    SCANS[result.scan_id] = result
    _save_job(result)
    _trim_scans()
    return {
        "scan_id": result.scan_id,
        "items": [item.public(result.scan_id) for item in result.items],
        "count": len(result.items),
        "ready": sum(1 for item in result.items if item.status == "ready"),
    }


@app.post("/api/admin/upload")
async def upload(request: Request, filename: str = Query(..., max_length=180)) -> dict[str, Any]:
    safe_name = Path(filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ingest.IMAGE_EXTENSIONS:
        raise HTTPException(400, "unsupported image extension")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty upload")
    if len(body) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large")
    target = cfg().root / "data" / "admin" / "uploads" / uuid.uuid4().hex
    target.mkdir(parents=True, exist_ok=True)
    path = target / safe_name
    path.write_bytes(body)
    result = ingest.scan_single(path, root=cfg().root)
    SCANS[result.scan_id] = result
    _save_job(result)
    _trim_scans()
    return {
        "scan_id": result.scan_id,
        "items": [item.public(result.scan_id) for item in result.items],
        "count": len(result.items),
        "ready": sum(1 for item in result.items if item.status == "ready"),
    }


@app.get("/api/admin/scans/{scan_id}/preview/{token}")
def scan_preview(scan_id: str, token: str) -> FileResponse:
    scan = SCANS.get(scan_id)
    if scan is None:
        raise HTTPException(404, "scan not found")
    item = next((entry for entry in scan.items if entry.token == token), None)
    if item is None or not item.source_path.is_file():
        raise HTTPException(404, "preview not found")
    return FileResponse(item.source_path)


@app.get("/api/admin/scans")
def list_scans() -> dict[str, Any]:
    items = []
    for scan in sorted(SCANS.values(), key=lambda item: item.created_at, reverse=True):
        ready = sum(1 for entry in scan.items if entry.status == "ready")
        items.append(
            {
                "scan_id": scan.scan_id,
                "created_at": scan.created_at,
                "source_dir": str(scan.source_dir or ""),
                "count": len(scan.items),
                "ready": ready,
            }
        )
    return {"items": items}


@app.get("/api/admin/scans/{scan_id}")
def get_scan(scan_id: str) -> dict[str, Any]:
    scan = SCANS.get(scan_id)
    if scan is None:
        raise HTTPException(404, "scan not found")
    return {
        "scan_id": scan.scan_id,
        "items": [item.public(scan.scan_id) for item in scan.items],
        "count": len(scan.items),
        "ready": sum(1 for item in scan.items if item.status == "ready"),
    }


@app.post("/api/admin/scans/{scan_id}/commit")
def commit(scan_id: str, body: CommitRequest) -> dict[str, Any]:
    scan = SCANS.get(scan_id)
    if scan is None:
        raise HTTPException(404, "scan not found")
    try:
        result = ingest.commit_scan(
            scan,
            [item.model_dump() for item in body.items],
            root=cfg().root,
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    if result["imported"] and not result["failed"]:
        # The public API can reload this catalog automatically when started
        # with MEME_DEV_RELOAD=1.  Don't fail the import if that process is down.
        _delete_job(scan_id)
    return result


@app.post("/api/admin/sync-data")
def sync_data() -> dict[str, Any]:
    """Refresh frontend/src/data/gallery.json after an import/edit."""
    pnpm = shutil.which("pnpm")
    if not pnpm:
        raise HTTPException(503, "pnpm is not available on PATH")
    try:
        proc = subprocess.run(
            [pnpm, "--dir", "frontend", "sync-data"],
            cwd=cfg().root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(504, "sync-data timed out") from exc
    if proc.returncode != 0:
        raise HTTPException(500, (proc.stderr or proc.stdout or "sync-data failed")[-2000:])
    return {"ok": True, "output": (proc.stdout or "")[-2000:]}


# ---------------------------------------------------------------------------
# Submission inbox (local-only foundation; the public writer can reuse these
# rules when the website submission form is added).


@app.post("/api/submissions")
async def create_submission(
    request: Request,
    filename: str = Query(..., max_length=180),
    name: str = Query("", max_length=120),
    tag: str = Query("", max_length=300),
    source: str = Query("", max_length=500),
    contact: str = Query("", max_length=300),
    rights_accepted: bool = Query(False),
) -> dict[str, Any]:
    body = await request.body()
    try:
        result = submissions.create_submission(
            body,
            filename=filename,
            cfg=cfg(),
            name=name,
            tags=[item.strip() for item in tag.split(",") if item.strip()],
            source=source,
            contact=contact,
            rights_accepted=rights_accepted,
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return result


@app.get("/api/admin/submissions")
def admin_submissions(status: str = "") -> dict[str, Any]:
    return {"items": submissions.list_submissions(cfg(), status=status)}


@app.get("/api/admin/submissions/{submission_id}/preview")
def submission_preview(submission_id: str) -> FileResponse:
    try:
        path = submissions.preview_path(cfg(), submission_id)
    except ValidationError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path)


@app.post("/api/admin/submissions/{submission_id}/approve")
def approve_submission(submission_id: str, body: SubmissionApprove) -> dict[str, Any]:
    try:
        return submissions.approve_submission(
            cfg(),
            submission_id,
            name=body.name,
            tags=body.tag,
            publish=body.publish,
        )
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/admin/submissions/{submission_id}/reject")
def reject_submission(submission_id: str, body: SubmissionReject) -> dict[str, Any]:
    try:
        return submissions.reject_submission(cfg(), submission_id, body.reason)
    except ValidationError as exc:
        raise HTTPException(400, str(exc)) from exc


