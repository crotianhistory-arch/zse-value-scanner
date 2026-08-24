from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Settings
from .errors import AccessBlocked, RemoteDataError


BLOCK_MARKERS = (
    "captcha",
    "cf-chl-",
    "cloudflare",
    "access denied",
    "verify you are human",
    "robot check",
)


class RespectfulHttpClient:
    """Small HTTP client with rate limiting, retries and block detection.

    It intentionally does not crawl HTML pages. The main source is the official
    EHO machine-readable feed and direct report files referenced by that feed.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._last_request_monotonic = 0.0
        self.session = requests.Session()
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))
        self.session.headers.update({"User-Agent": settings.user_agent, "Accept": "*/*"})

    def _wait_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_request_monotonic
        remaining = self.settings.min_request_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def get(self, url: str, *, params: dict | None = None, stream: bool = False) -> requests.Response:
        self._wait_rate_limit()
        response = self.session.get(
            url,
            params=params,
            timeout=self.settings.timeout_seconds,
            stream=stream,
        )
        self._last_request_monotonic = time.monotonic()
        self._check_response(response)
        return response

    def _check_response(self, response: requests.Response) -> None:
        if response.status_code in (403, 429):
            raise AccessBlocked(
                f"Remote endpoint returned HTTP {response.status_code}. "
                "Stop and reduce request rate or inspect server policy before retrying."
            )
        if response.status_code >= 400:
            raise RemoteDataError(f"HTTP {response.status_code} for {response.url}")

        ctype = (response.headers.get("content-type") or "").lower()
        if "text/html" in ctype and not response.raw.closed:
            try:
                sample = response.text[:10000].lower()
            except Exception:
                sample = ""
            if any(marker in sample for marker in BLOCK_MARKERS):
                raise AccessBlocked(f"Possible anti-bot challenge returned by {response.url}")

    def get_json(self, url: str, *, params: dict | None = None) -> dict:
        response = self.get(url, params=params)
        ctype = (response.headers.get("content-type") or "").lower()
        if "json" not in ctype:
            raise RemoteDataError(
                f"Expected JSON from {response.url}, got content-type {ctype or 'unknown'}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteDataError(f"Invalid JSON from {response.url}") from exc
        if not isinstance(payload, dict):
            raise RemoteDataError(f"Expected JSON object from {response.url}")
        return payload

    def download(self, url: str, destination: Path, *, max_bytes: int | None = None) -> tuple[int, str]:
        destination.parent.mkdir(parents=True, exist_ok=True)
        response = self.get(url, stream=True)
        digest = hashlib.sha256()
        total = 0
        tmp = destination.with_suffix(destination.suffix + ".part")
        try:
            with tmp.open("wb") as fh:
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise RemoteDataError(
                            f"Download exceeded configured limit ({max_bytes} bytes): {url}"
                        )
                    digest.update(chunk)
                    fh.write(chunk)
            tmp.replace(destination)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return total, digest.hexdigest()


def safe_filename_from_url(url: str, fallback: str = "document.bin") -> str:
    name = Path(urlparse(url).path).name
    return name or fallback
