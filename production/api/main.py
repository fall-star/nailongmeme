"""Production FastAPI service for the meme library.

Data layout expected under MEME_DATA_DIR (default /data):
    memes.json
    data/catalog.json
    data/search/aliases.json
    data/search/pinyin.json
    img/converted/<id>.jpg|gif

The app only reads data; it never writes to the library.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from meme_pipeline.search import SearchEngine
from meme_pipeline.imaging import extension_for, inspect_image
from meme_pipeline.maintenance import clean_tag
from meme_pipeline.util import ValidationError, atomic_write_json, clean_name, iso_local, sha256_file

LOGGER = logging.getLogger("meme_api")
ID_RE = re.compile(r"^[0-9]{1,5}$")
ID_QUERY_RE = re.compile(r"^\s*id\s*[-_ ]?\s*([0-9]{1,5})\s*$", re.IGNORECASE)
VERSION = "1.3.0"


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cors_origins: tuple[str, ...]
    public_base_url: str | None
    image_cache_seconds: int
    memes_cache_seconds: int
    default_min_score: float
    default_window: float
    dev_reload: bool


def _load_settings() -> Settings:
    raw_data_dir = Path(os.getenv("MEME_DATA_DIR", "/data")).expanduser()
    data_dir = raw_data_dir.resolve()
    origins = tuple(
        origin.strip()
        for origin in os.getenv("MEME_CORS_ORIGINS", "*").split(",")
        if origin.strip()
    ) or ("*",)
    public_base = os.getenv("MEME_PUBLIC_BASE_URL", "").strip() or None
    try:
        min_score = float(os.getenv("MEME_SEARCH_MIN_SCORE", "0.80"))
    except ValueError:
        min_score = 0.80
    try:
        window = float(os.getenv("MEME_SEARCH_WINDOW", "0.04"))
    except ValueError:
        window = 0.04
    return Settings(
        data_dir=data_dir,
        cors_origins=origins,
        public_base_url=public_base.rstrip("/") if public_base else None,
        image_cache_seconds=max(0, _env_int("MEME_IMAGE_CACHE_SECONDS", 31536000)),
        memes_cache_seconds=max(0, _env_int("MEME_MEMES_CACHE_SECONDS", 300)),
        default_min_score=min(1.0, max(0.0, min_score)),
        default_window=max(0.0, window),
        dev_reload=os.getenv("MEME_DEV_RELOAD", "0") == "1",
    )


SETTINGS = _load_settings()


class Library:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.catalog_path = data_dir / "data" / "catalog.json"
        self.memes_path = data_dir / "memes.json"
        self.converted_dir = data_dir / "img" / "converted"
        self.aliases_path = data_dir / "data" / "search" / "aliases.json"
        self.pinyin_path = data_dir / "data" / "search" / "pinyin.json"
        self.thumb_dir = data_dir / "img" / "thumbs"
        self.review_path = data_dir / "data" / "review" / "sensitive.json"

        if not self.catalog_path.is_file():
            raise RuntimeError(f"catalog not found: {self.catalog_path}")
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        if not isinstance(catalog, list):
            raise RuntimeError("catalog must be an array")
        # Library rule: every entry without 奶蛙 is considered 奶龙.
        # Keep the source catalog unchanged on disk; expose the derived tag at runtime.
        for entry in catalog:
            tags = [str(tag) for tag in entry.get("tag", []) if str(tag).strip()]
            if "奶蛙" not in tags and "奶龙" not in tags:
                tags.append("奶龙")
            entry["tag"] = tags
        self.catalog: list[dict[str, Any]] = catalog
        self.by_id = {str(entry["id"]): entry for entry in catalog}
        self.image_map: dict[str, Path] = {}
        for entry in catalog:
            if entry.get("pending") is True:
                continue
            if str(entry.get("status", "published")) != "published":
                continue
            path = self.image_path(str(entry["id"]))
            if path is not None:
                self.image_map[str(entry["id"])] = path

        self.review_status = "not_configured"
        self.blocked_tags: set[str] = set()
        self.blocked_ids: set[str] = set()
        if self.review_path.is_file():
            try:
                review = json.loads(self.review_path.read_text(encoding="utf-8"))
                self.review_status = str(review.get("review_status", "configured"))
                self.blocked_tags = {str(tag) for tag in review.get("blocked_tags", []) if str(tag).strip()}
                self.blocked_ids = {str(image_id) for image_id in review.get("blocked_ids", []) if str(image_id).strip()}
            except (OSError, json.JSONDecodeError, TypeError) as exc:
                LOGGER.warning("could not load review config %s: %s", self.review_path, exc)
        for entry in catalog:
            image_id = str(entry.get("id", ""))
            if any(str(tag) in self.blocked_tags for tag in entry.get("tag", [])):
                self.blocked_ids.add(image_id)
            if str(entry.get("status", "published")) == "blocked":
                self.blocked_ids.add(image_id)
        self.public_entries = [
            entry
            for entry in catalog
            if str(entry.get("id", "")) not in self.blocked_ids
            and str(entry.get("id", "")) in self.image_map
        ]
        self.public_by_id = {str(entry["id"]): entry for entry in self.public_entries}
        self.engine = SearchEngine(
            data_dir,
            self.public_entries,
            aliases_path=self.aliases_path,
            pinyin_path=self.pinyin_path,
            require_images=True,
        )
        self.random_entries = list(self.public_entries)
        self.memes_count = 0
        if self.memes_path.is_file():
            try:
                memes = json.loads(self.memes_path.read_text(encoding="utf-8"))
                self.memes_count = len(memes) if isinstance(memes, list) else 0
            except (OSError, json.JSONDecodeError):
                self.memes_count = 0

    def image_path(self, image_id: str) -> Path | None:
        if not ID_RE.fullmatch(image_id):
            return None
        for suffix in (".gif", ".jpg"):
            candidate = self.converted_dir / f"{image_id}{suffix}"
            if candidate.is_file():
                return candidate
        return None

    def is_blocked(self, image_id: str) -> bool:
        return str(image_id) in self.blocked_ids

    def thumb_path(self, image_id: str, size: int) -> Path | None:
        if not ID_RE.fullmatch(image_id) or size not in (320, 640):
            return None
        path = self.thumb_dir / str(size) / f"{image_id}.webp"
        return path if path.is_file() else None

    def random_entry(self, seed: int | None = None) -> dict[str, Any]:
        if not self.random_entries:
            raise HTTPException(status_code=503, detail="no images available")
        if seed is None:
            return secrets.choice(self.random_entries)
        return random.Random(seed).choice(self.random_entries)


class ImageInfo(BaseModel):
    id: str
    name: str
    tag: list[str]
    image_url: str
    image_download_url: str
    image_path: str
    width: int | None = None
    height: int | None = None
    format: str | None = None
    source_format: str | None = None
    bytes: int | None = None
    thumb_url: str | None = None
    thumb_320_url: str | None = None
    thumb_640_url: str | None = None


class GalleryItem(ImageInfo):
    pass


class GalleryResponse(BaseModel):
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    items: list[GalleryItem]


class SearchListItem(ImageInfo):
    score: float = Field(ge=0.0)
    method: str
    matched_field: str | None
    matched_value: str | None
    canonical: str | None


class SearchListResponse(BaseModel):
    query: str
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)
    fallback: bool
    fallback_reason: str | None = None
    items: list[SearchListItem]


class SearchResponse(ImageInfo):
    query: str
    matched_field: str | None
    matched_value: str | None
    canonical: str | None
    score: float = Field(ge=0.0)
    method: str
    fallback: bool
    candidate_count: int = Field(ge=0)


class RandomResponse(ImageInfo):
    pass


class HealthResponse(BaseModel):
    status: str
    version: str
    catalog_entries: int
    images: int
    public_images: int
    hidden_images: int
    review_status: str
    memes_count: int
    search_ready: bool


class TagSuggestionRequest(BaseModel):
    image_id: str = Field(min_length=1, max_length=8)
    tags: list[str] = Field(min_length=1, max_length=5)
    note: str = Field(default="", max_length=300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOGGER.info("loading meme library from %s", SETTINGS.data_dir)
    app.state.library = Library(SETTINGS.data_dir)
    try:
        app.state.library_mtime = app.state.library.catalog_path.stat().st_mtime_ns
    except OSError:
        app.state.library_mtime = None
    LOGGER.info(
        "library ready: %s entries, %s images",
        len(app.state.library.catalog),
        len(app.state.library.image_map),
    )
    yield


app = FastAPI(
    title="Meme Library API",
    description="Nailong / Nai-hub meme library: fuzzy search, random image, JSON catalog and image download.",
    version=VERSION,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(SETTINGS.cors_origins),
    allow_credentials="*" not in SETTINGS.cors_origins,
    allow_methods=["GET", "HEAD", "OPTIONS", "POST"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "ETag", "Last-Modified"],
    max_age=86400,
)


@app.middleware("http")
async def no_store_for_dynamic_api(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/search", "/api/random", "/api/health")):
        response.headers["Cache-Control"] = "no-store"
    return response

router = APIRouter(prefix="/api")


def _get_library(request: Request) -> Library:
    library = getattr(request.app.state, "library", None)
    if library is None:
        raise HTTPException(status_code=503, detail="library not ready")
    if SETTINGS.dev_reload:
        try:
            mtime = library.catalog_path.stat().st_mtime_ns
        except OSError:
            mtime = None
        if mtime is not None and mtime != getattr(request.app.state, "library_mtime", None):
            try:
                library = Library(SETTINGS.data_dir)
            except Exception as exc:  # noqa: BLE001 - keep the old in-memory copy
                LOGGER.exception("dev reload failed: %s", exc)
                raise HTTPException(status_code=503, detail="dev reload failed") from exc
            request.app.state.library = library
            request.app.state.library_mtime = mtime
            LOGGER.info("dev reload: %s entries", len(library.catalog))
    return library


def _image_url(request: Request, image_id: str) -> str:
    if SETTINGS.public_base_url:
        return f"{SETTINGS.public_base_url}/api/images/{image_id}"
    return str(request.url_for("image", image_id=image_id))


def _thumb_url(request: Request, image_id: str, size: int) -> str | None:
    library = _get_library(request)
    if library.thumb_path(image_id, size) is None:
        return None
    if SETTINGS.public_base_url:
        return f"{SETTINGS.public_base_url}/api/thumbs/{image_id}/{size}"
    return str(request.url_for("thumb", image_id=image_id, size=size))


def _image_payload(request: Request, entry: dict[str, Any]) -> dict[str, Any]:
    image_id = str(entry["id"])
    url = _image_url(request, image_id)
    library = _get_library(request)
    path = library.image_map.get(image_id)
    thumb_320 = _thumb_url(request, image_id, 320)
    thumb_640 = _thumb_url(request, image_id, 640)
    return {
        "id": image_id,
        "name": entry.get("name", ""),
        "tag": list(entry.get("tag", [])),
        "image_url": url,
        "image_download_url": f"{url}?download=1",
        "image_path": str(path.relative_to(library.data_dir)) if path else "",
        "width": entry.get("width"),
        "height": entry.get("height"),
        "format": "gif" if path and path.suffix.lower() == ".gif" else "jpeg",
        "source_format": entry.get("format"),
        "bytes": path.stat().st_size if path else entry.get("bytes"),
        "thumb_url": thumb_640 or thumb_320 or url,
        "thumb_320_url": thumb_320,
        "thumb_640_url": thumb_640,
    }


def _image_file(
    path: Path,
    *,
    cache_control: str,
    download: bool = False,
    filename: str | None = None,
) -> FileResponse:
    """Return only the image bytes (plus standard HTTP headers)."""
    suffix = path.suffix.lower()
    media_type = "image/gif" if suffix == ".gif" else "image/jpeg"
    headers = {
        "Cache-Control": cache_control,
        "X-Content-Type-Options": "nosniff",
    }
    if download:
        name = filename or f"{path.stem}{suffix}"
        headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return FileResponse(path, media_type=media_type, headers=headers)


def _explicit_id_query(query: str) -> str | None:
    match = ID_QUERY_RE.fullmatch(str(query or ""))
    if not match:
        return None
    return str(int(match.group(1), 10))


def _public_entry_by_id(library: "Library", image_id: str) -> dict[str, Any] | None:
    entry = library.public_by_id.get(image_id)
    if entry is not None:
        return entry
    # Tolerate catalogs that use zero-padded IDs in the future.
    for candidate in library.public_entries:
        if str(candidate.get("id", "")).isdigit() and int(candidate["id"]) == int(image_id):
            return candidate
    return None


def _search(
    request: Request,
    q: str,
    min_score: float,
    window: float,
    seed: int | None,
    stable: bool,
) -> dict[str, Any]:
    library = _get_library(request)
    explicit_id = _explicit_id_query(q)
    if explicit_id is not None:
        entry = _public_entry_by_id(library, explicit_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="image by id not found")
        payload = _image_payload(request, entry)
        payload.update(
            {
                "query": q,
                "matched_field": "id",
                "matched_value": explicit_id,
                "canonical": None,
                "score": 1.0,
                "method": "id",
                "fallback": False,
                "candidate_count": 1,
            }
        )
        return payload
    try:
        result = library.engine.search(
            q,
            min_score=min_score,
            random_window=window,
            seed=seed,
            stable=stable,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    entry = library.by_id.get(str(result["id"]))
    if entry is None:
        raise HTTPException(status_code=500, detail="search returned unknown id")
    payload = _image_payload(request, entry)
    payload.update(
        {
            "query": q,
            "matched_field": result.get("matched_field"),
            "matched_value": result.get("matched_value"),
            "canonical": result.get("canonical"),
            "score": result.get("score", 0.0),
            "method": result.get("method", "fallback"),
            "fallback": bool(result.get("fallback")),
            "candidate_count": int(result.get("candidate_count") or 0),
        }
    )
    return payload


@router.get("/health", response_model=HealthResponse, summary="Health and library counts")
def health(request: Request) -> HealthResponse:
    library = _get_library(request)
    return HealthResponse(
        status="ok",
        version=VERSION,
        catalog_entries=len(library.catalog),
        images=len(library.image_map),
        public_images=len(library.public_entries),
        hidden_images=len(library.blocked_ids),
        review_status=library.review_status,
        memes_count=library.memes_count,
        search_ready=bool(library.engine.entries),
    )


@router.get(
    "/gallery",
    response_model=GalleryResponse,
    summary="Public gallery metadata with thumbnails and pagination",
)
def gallery(
    request: Request,
    q: str | None = Query(None, max_length=64, description="Local name/tag substring filter"),
    tag: str | None = Query(None, max_length=64, description="Exact tag filter"),
    tags: str | None = Query(None, max_length=256, description="Comma-separated tags; all must match"),
    file_format: str | None = Query(None, alias="format", pattern="^(jpeg|gif)$"),
    limit: int = Query(48, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> GalleryResponse:
    library = _get_library(request)
    entries = list(library.public_entries)
    if tag:
        entries = [entry for entry in entries if tag in entry.get("tag", [])]
    if tags:
        wanted = [value.strip() for value in tags.split(",") if value.strip()]
        entries = [
            entry
            for entry in entries
            if all(value in entry.get("tag", []) for value in wanted)
        ]
    if q:
        query = q.strip().casefold()
        if query:
            entries = [
                entry
                for entry in entries
                if query in str(entry.get("name", "")).casefold()
                or any(query in str(item).casefold() for item in entry.get("tag", []))
            ]
    if file_format:
        entries = [
            entry
            for entry in entries
            if (library.image_map.get(str(entry["id"])) or Path("")).suffix.lower()
            == (".gif" if file_format == "gif" else ".jpg")
        ]
    total = len(entries)
    entries.sort(key=lambda entry: int(entry["id"]))
    page = entries[offset : offset + limit]
    return GalleryResponse(
        total=total,
        limit=limit,
        offset=offset,
        items=[GalleryItem(**_image_payload(request, entry)) for entry in page],
    )


@router.get(
    "/gallery/{image_id}",
    response_model=GalleryItem,
    summary="Metadata for one public image",
)
def gallery_item(request: Request, image_id: str) -> GalleryItem:
    library = _get_library(request)
    if not ID_RE.fullmatch(image_id) or image_id not in library.public_by_id:
        raise HTTPException(status_code=404, detail="image not found")
    return GalleryItem(**_image_payload(request, library.public_by_id[image_id]))


@router.get(
    "/thumbs/{image_id}/{size}",
    name="thumb",
    summary="WebP card thumbnail (320 or 640)",
    responses={200: {"content": {"image/webp": {}}, "description": "WebP thumbnail"}},
)
def thumb(request: Request, image_id: str, size: int) -> FileResponse:
    library = _get_library(request)
    if (
        image_id not in library.image_map
        or library.is_blocked(image_id)
        or size not in (320, 640)
    ):
        raise HTTPException(status_code=404, detail="thumbnail not found")
    path = library.thumb_path(image_id, size)
    if path is None:
        raise HTTPException(status_code=404, detail="thumbnail not found")
    return FileResponse(
        path,
        media_type="image/webp",
        headers={
            "Cache-Control": f"public, max-age={SETTINGS.image_cache_seconds}, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/memes.public.json",
    summary="Public 1164-entry index, excluding blocked review items",
)
def memes_public(request: Request) -> JSONResponse:
    library = _get_library(request)
    payload = [
        {"id": str(entry["id"]), "name": entry.get("name", ""), "tag": list(entry.get("tag", []))}
        for entry in library.public_entries
    ]
    return JSONResponse(
        content=payload,
        headers={
            "Cache-Control": f"public, max-age={SETTINGS.memes_cache_seconds}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/search/list",
    response_model=SearchListResponse,
    summary="Multi-result keyword search metadata (ranked; no random fallback)",
)
def search_list(
    request: Request,
    q: str = Query(..., min_length=1, max_length=64),
    min_score: float = Query(SETTINGS.default_min_score, ge=0, le=1),
    limit: int = Query(40, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> SearchListResponse:
    library = _get_library(request)
    explicit_id = _explicit_id_query(q)
    if explicit_id is not None:
        entry = _public_entry_by_id(library, explicit_id)
        if entry is None or offset > 0:
            return SearchListResponse(
                query=q,
                total=0,
                limit=limit,
                offset=offset,
                fallback=False,
                fallback_reason="id_not_found" if entry is None else None,
                items=[],
            )
        payload = _image_payload(request, entry)
        payload.update(
            {
                "score": 1.0,
                "method": "id",
                "matched_field": "id",
                "matched_value": explicit_id,
                "canonical": None,
            }
        )
        return SearchListResponse(
            query=q,
            total=1,
            limit=limit,
            offset=offset,
            fallback=False,
            fallback_reason=None,
            items=[SearchListItem(**payload)],
        )
    try:
        result = library.engine.search_many(
            q, min_score=min_score, limit=limit, offset=offset
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    items: list[SearchListItem] = []
    for item in result["items"]:
        entry = library.public_by_id.get(str(item["id"]))
        if entry is None:
            continue
        payload = _image_payload(request, entry)
        payload.update(
            {
                "score": item.get("score", 0.0),
                "method": item.get("method", "unknown"),
                "matched_field": item.get("matched_field"),
                "matched_value": item.get("matched_value"),
                "canonical": item.get("canonical"),
            }
        )
        items.append(SearchListItem(**payload))
    return SearchListResponse(
        query=q,
        total=int(result.get("total", 0)),
        limit=limit,
        offset=offset,
        fallback=bool(result.get("fallback")),
        fallback_reason=result.get("fallback_reason"),
        items=items,
    )


@router.get(
    "/search",
    summary="Keyword search: return the matched image bytes",
    responses={200: {"content": {"image/jpeg": {}, "image/gif": {}}, "description": "Raw JPG/GIF image"}},
)
def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=64, description="Keyword or Chinese homophone"),
    min_score: float = Query(SETTINGS.default_min_score, ge=0, le=1, description="Below this score, return a random image"),
    window: float = Query(SETTINGS.default_window, ge=0, le=0.5, description="Randomize among scores within this window of the best"),
    seed: int | None = Query(None, description="Optional seed for reproducible random selection"),
    stable: bool = Query(False, description="Choose the lowest id in the top group instead of random"),
) -> FileResponse:
    library = _get_library(request)
    payload = _search(request, q, min_score, window, seed, stable)
    path = library.image_path(str(payload["id"]))
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _image_file(path, cache_control="no-store")


@router.get(
    "/search/meta",
    response_model=SearchResponse,
    summary="Keyword search metadata (JSON); use /search for the image itself",
)
def search_meta(
    request: Request,
    q: str = Query(..., min_length=1, max_length=64),
    min_score: float = Query(SETTINGS.default_min_score, ge=0, le=1),
    window: float = Query(SETTINGS.default_window, ge=0, le=0.5),
    seed: int | None = Query(None),
    stable: bool = Query(False),
) -> SearchResponse:
    return SearchResponse(**_search(request, q, min_score, window, seed, stable))


@router.get(
    "/search/image",
    summary="Alias of /api/search: return the matched image bytes",
    responses={200: {"content": {"image/jpeg": {}, "image/gif": {}}, "description": "Raw JPG/GIF image"}},
)
def search_image(
    request: Request,
    q: str = Query(..., min_length=1, max_length=64),
    min_score: float = Query(SETTINGS.default_min_score, ge=0, le=1),
    window: float = Query(SETTINGS.default_window, ge=0, le=0.5),
    seed: int | None = Query(None),
    stable: bool = Query(False),
) -> FileResponse:
    return search(request, q, min_score, window, seed, stable)


@router.get(
    "/random",
    summary="Return one random image (raw JPG/GIF bytes)",
    responses={200: {"content": {"image/jpeg": {}, "image/gif": {}}, "description": "Raw JPG/GIF image"}},
)
def random_image(
    request: Request,
    seed: int | None = Query(None, description="Optional seed for reproducible selection"),
) -> FileResponse:
    library = _get_library(request)
    entry = library.random_entry(seed)
    path = library.image_path(str(entry["id"]))
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _image_file(path, cache_control="no-store")


@router.get(
    "/random/meta",
    response_model=RandomResponse,
    summary="Random image metadata (JSON); use /random for the image itself",
)
def random_meta(
    request: Request,
    seed: int | None = Query(None),
) -> RandomResponse:
    library = _get_library(request)
    entry = library.random_entry(seed)
    return RandomResponse(**_image_payload(request, entry))


@router.get(
    "/random/image",
    summary="Alias of /api/random: return a random image",
    responses={200: {"content": {"image/jpeg": {}, "image/gif": {}}, "description": "Raw JPG/GIF image"}},
)
def random_image_redirect(
    request: Request,
    seed: int | None = Query(None),
) -> FileResponse:
    return random_image(request, seed)


@router.get("/images/{image_id}", name="image", summary="Download or display one image by id")
def image(
    request: Request,
    image_id: str,
    download: bool = Query(False, description="Send as attachment instead of inline"),
) -> FileResponse:
    library = _get_library(request)
    if image_id not in library.image_map or library.is_blocked(image_id):
        raise HTTPException(status_code=404, detail="image not found")
    path = library.image_path(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")
    return _image_file(
        path,
        cache_control=f"public, max-age={SETTINGS.image_cache_seconds}, immutable",
        download=download,
        filename=f"{image_id}{path.suffix.lower()}",
    )


@router.get("/memes.json", summary="Download the slim memes.json index")
def memes_json(request: Request) -> JSONResponse:
    library = _get_library(request)
    if not library.memes_path.is_file():
        raise HTTPException(status_code=503, detail="memes.json is not available")
    payload = [
        {"id": str(entry["id"]), "name": entry.get("name", ""), "tag": list(entry.get("tag", []))}
        for entry in library.catalog
        if str(entry.get("status", "published")) == "published"
    ]
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": 'attachment; filename="memes.json"',
            "Cache-Control": f"public, max-age={SETTINGS.memes_cache_seconds}",
            "X-Content-Type-Options": "nosniff",
        },
    )


# Public write endpoints.  They write only to quarantined inboxes; neither
# endpoint mutates the catalog or returns a public image URL.  Admin review and
# a later local->production sync are required before anything becomes public.


def _submission_dir() -> Path:
    path = SETTINGS.data_dir / "data" / "submissions"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tag_suggestion_dir() -> Path:
    path = SETTINGS.data_dir / "data" / "tag-suggestions"
    path.mkdir(parents=True, exist_ok=True)
    return path


@router.post("/submissions", status_code=201, summary="Submit a meme image for review")
async def public_submit(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=180),
    name: str = Query("", max_length=120),
    tag: str = Query("", max_length=300),
    source: str = Query("", max_length=500),
    contact: str = Query("", max_length=300),
    rights_accepted: bool = Query(False),
) -> JSONResponse:
    if not rights_accepted:
        raise HTTPException(status_code=400, detail="必须确认拥有图片投稿和展示权限")
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="投稿文件为空")
    if len(body) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="投稿文件不能超过 25 MB")
    safe_name = Path(filename).name or "submission.bin"
    if Path(safe_name).suffix.lower() not in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif"}:
        raise HTTPException(status_code=400, detail="不支持的图片格式")
    submission_id = uuid.uuid4().hex
    folder = _submission_dir() / submission_id
    folder.mkdir(parents=True, exist_ok=False)
    temp = folder / f"original{Path(safe_name).suffix.lower()}"
    try:
        temp.write_bytes(body)
        meta = inspect_image(temp)
        digest = sha256_file(temp)
        library = _get_library(request)
        duplicate_id = next(
            (str(entry["id"]) for entry in library.catalog if entry.get("sha256") == digest),
            None,
        )
        tags = [clean_tag(value.strip()) for value in tag.split(",") if value.strip()][:10]
        record = {
            "submission_id": submission_id,
            "status": "duplicate" if duplicate_id else "pending_review",
            "name": clean_name(name)[:120],
            "tag": list(dict.fromkeys(tags)),
            "source": source.strip()[:500],
            "contact": contact.strip()[:300],
            "rights_accepted": True,
            "original": temp.name,
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
    except (ValidationError, OSError) as exc:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(
        {"submission_id": submission_id, "status": record["status"]},
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/tag-suggestions", status_code=201, summary="Suggest tags for an existing public image")
def suggest_tags(payload: TagSuggestionRequest, request: Request) -> JSONResponse:
    library = _get_library(request)
    image_id = payload.image_id.strip()
    if image_id not in library.public_by_id:
        raise HTTPException(status_code=404, detail="image not found")
    tags: list[str] = []
    try:
        for value in payload.tags:
            tag = clean_tag(str(value).strip())
            if tag not in tags:
                tags.append(tag)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not tags:
        raise HTTPException(status_code=400, detail="至少提交一个标签")
    suggestion_id = uuid.uuid4().hex
    entry = library.public_by_id[image_id]
    record = {
        "suggestion_id": suggestion_id,
        "image_id": image_id,
        "image_name": entry.get("name", ""),
        "tags": tags,
        "note": payload.note.strip()[:300],
        "status": "pending_review",
        "created_at": iso_local(),
        "reviewed_at": None,
        "review_note": "",
    }
    atomic_write_json(_tag_suggestion_dir() / f"{suggestion_id}.json", record)
    return JSONResponse(
        {"suggestion_id": suggestion_id, "status": "pending_review"},
        status_code=201,
        headers={"Cache-Control": "no-store"},
    )


app.include_router(router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/api/docs", status_code=302)
