from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:  # Windows
    import msvcrt
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]


class PipelineError(RuntimeError):
    """Base exception for expected pipeline failures."""


class LockError(PipelineError):
    """Raised when another writer holds the library lock."""


class ValidationError(PipelineError):
    """Raised when data on disk does not satisfy the documented schema."""


# ---------------------------------------------------------------------------
# Time helpers


def now_local() -> dt.datetime:
    return dt.datetime.now().astimezone().replace(microsecond=0)


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_local(value: dt.datetime | None = None) -> str:
    return (value or now_local()).isoformat()


def utc_stamp(value: dt.datetime | None = None) -> str:
    return (value or now_utc()).astimezone(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_run_id() -> str:
    return f"{utc_stamp()}-{uuid.uuid4().hex[:8]}"


def parse_iso(value: str) -> dt.datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing ``Z``."""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed


# ---------------------------------------------------------------------------
# JSON and atomic files


def json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    ).encode("utf-8")


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}") from exc


def _fsync_dir(path: Path) -> None:
    """Best-effort directory fsync so a rename survives a sudden power loss."""
    if os.name != "posix":
        return
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
        _fsync_dir(path.parent)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise


def atomic_write_json(path: Path, value: Any, mode: int = 0o644) -> None:
    atomic_write_bytes(path, json_bytes(value), mode=mode)


def atomic_write_text(path: Path, text: str, mode: int = 0o644) -> None:
    atomic_write_bytes(path, text.encode("utf-8"), mode=mode)


# ---------------------------------------------------------------------------
# Hashing


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# Text helpers


_WHITESPACE_RE = re.compile(r"\s+")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_name(raw: Any, fallback: str = "") -> str:
    """Normalise visible text without changing its meaning.

    HTML entities are decoded, Unicode line/paragraph separators and tabs are
    treated as spaces, and runs of whitespace collapse to one space.  CJK text
    is untouched.
    """
    if raw is None:
        text = ""
    else:
        text = str(raw)
    text = html.unescape(text)
    text = text.replace("\u3000", " ").replace("\u2028", " ").replace("\u2029", " ")
    text = _CONTROL_RE.sub(" ", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or fallback


def short(text: str, limit: int = 160) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def human_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


# ---------------------------------------------------------------------------
# URL / path helpers


def is_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def ensure_within(root: Path, path: Path, label: str = "path") -> Path:
    root = root.resolve()
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValidationError(f"{label} escapes the project root: {path}") from exc
    return resolved


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


# ---------------------------------------------------------------------------
# Locking


class FileLock:
    """Exclusive, non-blocking advisory lock shared by every writer."""

    def __init__(self, path: Path, description: str = "library"):
        self.path = path
        self.description = description
        self._handle = None

    def __enter__(self) -> "FileLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+", encoding="utf-8")
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            elif msvcrt is not None:  # pragma: no cover - Windows
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover - exotic platform
                raise LockError("this platform does not support advisory file locks")
        except (BlockingIOError, OSError) as exc:
            self._handle.close()
            self._handle = None
            raise LockError(
                f"another process is writing to the {self.description}; "
                f"wait for it to finish or remove {self.path} if it crashed"
            ) from exc
        self._handle.seek(0)
        self._handle.truncate()
        self._handle.write(f"pid={os.getpid()} acquired={iso_local()}\n")
        self._handle.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:  # pragma: no cover - Windows
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()


# ---------------------------------------------------------------------------
# Backups


def prune_backups(directory: Path, pattern: str, keep: int) -> None:
    if keep <= 0 or not directory.exists():
        return
    files = sorted(
        (path for path in directory.glob(pattern) if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old in files[keep:]:
        with contextlib.suppress(OSError):
            old.unlink()


def iter_files(directory: Path) -> Iterator[Path]:
    if not directory.exists():
        return
    for path in directory.iterdir():
        if path.is_file():
            yield path
