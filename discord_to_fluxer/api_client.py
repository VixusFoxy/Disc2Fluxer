from __future__ import annotations

import re
import time
import logging
from typing import Callable

import httpx

log = logging.getLogger(__name__)

MAX_RETRIES = 8
MAX_RETRY_AFTER = 300  # Never sleep longer than 5 minutes on a 429.

_SNOWFLAKE_RE = re.compile(r"^[0-9]+$")

# Errors that indicate the server dropped us (rate limit without a proper 429).
_RETRIABLE_EXCEPTIONS = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ConnectError,
    httpx.ReadTimeout,
    ConnectionError,
)

LogFn = Callable[[str], None]


def validate_snowflake(value: str, label: str = "ID") -> str:
    """Validate that a value looks like a Discord/Fluxer snowflake ID."""
    if not _SNOWFLAKE_RE.match(value):
        raise ValueError(f"Invalid {label}: expected numeric snowflake, got {value!r}")
    return value


class APIClient:
    """Thin HTTP wrapper with header-based rate limit tracking."""

    def __init__(self, base_url: str, token: str, log_fn: LogFn | None = None) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bot {token}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        self._log_fn = log_fn

    def close(self) -> None:
        self._client.close()

    def _emit(self, msg: str) -> None:
        log.info(msg)
        if self._log_fn:
            self._log_fn(msg)

    # -- rate limit tracking from response headers -------------------------

    def _wait_if_needed(self, resp: httpx.Response) -> None:
        """Spread requests evenly across the rate limit window."""
        remaining = resp.headers.get("X-RateLimit-Remaining")
        reset = resp.headers.get("X-RateLimit-Reset")
        if remaining is None or reset is None:
            return
        try:
            remaining_int = int(remaining)
            reset_time = float(reset)
        except (ValueError, TypeError):
            return

        time_left = reset_time - time.time()
        if time_left <= 0 or remaining_int <= 0:
            # Bucket exhausted — wait for reset.
            if time_left > 0:
                self._emit(f"  Bucket empty, waiting {time_left:.1f}s for reset...")
                time.sleep(time_left + 0.1)
            return

        # Space remaining requests evenly across the remaining window.
        interval = time_left / remaining_int
        # Clamp to a reasonable range — at least 0.5s so it feels deliberate,
        # no more than 10s so it doesn't crawl.
        interval = max(0.5, min(interval, 10.0))
        time.sleep(interval)

    # -- core request with retry on 429 and connection drops ---------------

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        backoff = 1.0
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.request(method, path, **kwargs)
            except _RETRIABLE_EXCEPTIONS as e:
                wait = min(backoff, 30.0)
                self._emit(f"Connection dropped ({type(e).__name__}), retrying in {wait:.1f}s...")
                time.sleep(wait)
                backoff *= 2
                continue

            if resp.status_code == 429:
                # Prefer server's retry_after, fall back to header, then backoff.
                try:
                    retry_after = float(resp.json().get("retry_after", 0))
                except Exception:
                    retry_after = 0
                if retry_after <= 0:
                    retry_after_hdr = resp.headers.get("Retry-After")
                    retry_after = float(retry_after_hdr) if retry_after_hdr else backoff
                retry_after = min(retry_after, MAX_RETRY_AFTER)
                self._emit(f"Rate limited on {method} {path}, waiting {retry_after:.1f}s...")
                time.sleep(retry_after)
                backoff *= 2
                continue

            self._raise_with_body(resp)
            # Preemptively sleep if we're about to exhaust the bucket.
            if method != "GET":
                self._wait_if_needed(resp)
            return resp

        # Final attempt — let it raise naturally.
        resp = self._client.request(method, path, **kwargs)
        self._raise_with_body(resp)
        return resp

    @staticmethod
    def _raise_with_body(resp: httpx.Response) -> None:
        """Like raise_for_status but includes response body in the error."""
        if resp.is_success:
            return
        try:
            body = resp.json()
        except Exception:
            body = resp.text[:500]
        raise httpx.HTTPStatusError(
            f"{resp.status_code} {resp.reason_phrase}: {body}",
            request=resp.request,
            response=resp,
        )

    # -- convenience verbs ------------------------------------------------

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs) -> httpx.Response:
        return self._request("PATCH", path, **kwargs)

    def put(self, path: str, **kwargs) -> httpx.Response:
        return self._request("PUT", path, **kwargs)
