from __future__ import annotations

import email.utils
import hashlib
import http.client
import io
import logging
import os
import random
import socket
import ssl
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .util import PipelineError, ValidationError

logger = logging.getLogger("meme_pipeline.net")

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


class FetchError(PipelineError):
    pass


@dataclass(slots=True)
class DownloadResult:
    bytes_written: int
    sha256: str
    content_type: str
    final_url: str
    status: int


class HttpClient:
    """Small, dependency-free HTTP client with retries and hard size limits."""

    def __init__(self, config: Config, *, referer: str | None = None):
        self.config = config
        self.referer = referer
        self._local = threading.local()
        self._throttle_lock = threading.Lock()
        self._last_request = 0.0

    # -- opener ---------------------------------------------------------

    def _opener(self) -> urllib.request.OpenerDirector:
        opener = getattr(self._local, "opener", None)
        if opener is None:
            opener = urllib.request.build_opener()
            self._local.opener = opener
        return opener

    def _throttle(self) -> None:
        delay = self.config.request_delay_ms / 1000.0
        if delay <= 0:
            return
        with self._throttle_lock:
            now = time.monotonic()
            wait = self._last_request + delay - now
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _request(self, url: str, accept: str) -> urllib.request.Request:
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": accept,
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
            "Cache-Control": "no-cache",
        }
        if self.referer:
            headers["Referer"] = self.referer
        return urllib.request.Request(url, headers=headers, method="GET")

    # -- retry helpers --------------------------------------------------

    @staticmethod
    def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
        value = exc.headers.get("Retry-After") if exc.headers else None
        if not value:
            return None
        value = value.strip()
        try:
            return max(0.0, float(value))
        except ValueError:
            pass
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed is None:
            return None
        return max(0.0, parsed.timestamp() - time.time())

    @staticmethod
    def _is_retryable(exc: BaseException) -> bool:
        if isinstance(exc, urllib.error.HTTPError):
            return exc.code in RETRYABLE_STATUS
        if isinstance(exc, urllib.error.URLError):
            return True
        if isinstance(
            exc,
            (
                TimeoutError,
                ConnectionError,
                socket.timeout,
                http.client.IncompleteRead,
                http.client.RemoteDisconnected,
                ssl.SSLEOFError,
            ),
        ):
            return True
        return False

    @staticmethod
    def _body_hint(exc: urllib.error.HTTPError) -> str:
        try:
            raw = exc.read(256)
        except Exception:  # noqa: BLE001 - best effort diagnostics only
            return ""
        if not raw:
            return ""
        try:
            return raw.decode("utf-8", "replace").replace("\n", " ")[:200]
        except Exception:  # noqa: BLE001
            return ""

    def _sleep_before_retry(
        self,
        attempt: int,
        exc: BaseException,
        url: str,
    ) -> None:
        delay = min(60.0, 1.5**attempt) + random.uniform(0.0, 0.5)
        if isinstance(exc, urllib.error.HTTPError) and exc.code == 429:
            retry_after = self._retry_after_seconds(exc)
            if retry_after is not None:
                delay = max(delay, min(120.0, retry_after))
        logger.warning(
            "retrying %s in %.1fs (attempt %d/%d): %s",
            url,
            delay,
            attempt + 1,
            self.config.retries,
            exc,
        )
        time.sleep(delay)

    # -- public API -----------------------------------------------------

    def get_bytes(self, url: str, *, accept: str = "*/*", max_bytes: int | None = None) -> bytes:
        limit = max_bytes or self.config.max_bytes
        attempts = self.config.retries + 1
        last_error: BaseException | None = None
        for attempt in range(attempts):
            self._throttle()
            try:
                request = self._request(url, accept)
                with self._opener().open(request, timeout=self.config.timeout_seconds) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    length = response.headers.get("Content-Length")
                    if length and length.isdigit() and int(length) > limit:
                        raise FetchError(
                            f"{url}: response is {length} bytes, above the {limit}-byte limit"
                        )
                    buffer = io.BytesIO()
                    total = 0
                    while True:
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > limit:
                            raise FetchError(
                                f"{url}: response exceeded the {limit}-byte limit"
                            )
                        buffer.write(chunk)
                    return buffer.getvalue()
            except BaseException as exc:  # noqa: BLE001 - classified below
                last_error = exc
                if not self._is_retryable(exc) or attempt + 1 >= attempts:
                    break
                self._sleep_before_retry(attempt, exc, url)
        raise self._wrap_error(url, last_error)

    def download(self, url: str, dest: Path, *, accept: str = "image/*,*/*;q=0.8") -> DownloadResult:
        limit = self.config.max_bytes
        attempts = self.config.retries + 1
        last_error: BaseException | None = None
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")

        for attempt in range(attempts):
            self._throttle()
            part.unlink(missing_ok=True)
            try:
                request = self._request(url, accept)
                with self._opener().open(request, timeout=self.config.timeout_seconds) as response:
                    status = getattr(response, "status", None) or response.getcode()
                    length_header = response.headers.get("Content-Length")
                    if length_header and length_header.isdigit() and int(length_header) > limit:
                        raise FetchError(
                            f"{url}: image is {length_header} bytes, above the {limit}-byte limit"
                        )
                    digest = hashlib.sha256()
                    total = 0
                    with part.open("wb") as handle:
                        while True:
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            total += len(chunk)
                            if total > limit:
                                raise FetchError(
                                    f"{url}: image exceeded the {limit}-byte limit"
                                )
                            digest.update(chunk)
                            handle.write(chunk)
                        handle.flush()
                        os.fsync(handle.fileno())
                    if total == 0:
                        raise FetchError(f"{url}: empty response body")
                    os.replace(part, dest)
                    return DownloadResult(
                        bytes_written=total,
                        sha256=digest.hexdigest(),
                        content_type=response.headers.get("Content-Type", ""),
                        final_url=response.geturl(),
                        status=status,
                    )
            except BaseException as exc:  # noqa: BLE001 - classified below
                last_error = exc
                part.unlink(missing_ok=True)
                if not self._is_retryable(exc) or attempt + 1 >= attempts:
                    break
                self._sleep_before_retry(attempt, exc, url)

        part.unlink(missing_ok=True)
        raise self._wrap_error(url, last_error)

    def _wrap_error(self, url: str, exc: BaseException | None) -> FetchError:
        if exc is None:
            return FetchError(f"{url}: unknown fetch failure")
        if isinstance(exc, urllib.error.HTTPError):
            hint = self._body_hint(exc)
            suffix = f" body={hint!r}" if hint else ""
            return FetchError(f"{url}: HTTP {exc.code} {exc.reason}{suffix}")
        if isinstance(exc, urllib.error.URLError):
            return FetchError(f"{url}: {exc.reason}")
        if isinstance(exc, FetchError):
            return exc
        return FetchError(f"{url}: {type(exc).__name__}: {exc}")


def validate_http_url(url: str) -> None:
    from .util import is_http_url

    if not is_http_url(url):
        raise ValidationError(f"refusing non-http(s) URL: {url!r}")
