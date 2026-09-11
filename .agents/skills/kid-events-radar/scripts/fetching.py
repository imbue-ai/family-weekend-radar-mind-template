#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Fetch a source's raw payload: plain HTTP first, Fortress browser as fallback.

Failure of one source never sinks the run -- every fetch resolves to a RawFetch
carrying an ``ok`` / ``blocked`` / ``error`` status, so the caller can carry on
with a partial result and report which sources failed.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

from radar_config import (
    BROWSER_FETCH_HELPER_PATH,
    BROWSER_HARD_TIMEOUT_SECONDS,
    BROWSER_USER_AGENT,
    HTTP_HARD_TIMEOUT_SECONDS,
    REPO_ROOT,
)
from radar_types import FetchStatus, RawFetch, SourceConfig

# HTTP status codes that mean "bot-blocked / try the browser" rather than a
# genuine client error we should give up on.
_BLOCKING_STATUS_CODES = frozenset({403, 406, 429, 500, 502, 503, 520, 521, 526})


class _HttpBlocked(Exception):
    """The server returned a bot-block-style status."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class _HttpFailed(Exception):
    """The HTTP request could not complete at all."""


# Heuristics for "this HTTP response is a JavaScript shell; the real event
# listing only renders in a browser". A shell either has almost no visible text,
# or -- like a Next.js listing page whose nav renders but whose dated events do
# not -- has text but almost no calendar dates on what should be a listing page.
_CONTENT_LIGHT_VISIBLE_TEXT_THRESHOLD = 1500
_MIN_DATE_SIGNALS_FOR_LISTING = 3
_MONTH_NAME_PATTERN = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}",
    re.IGNORECASE,
)
_NUMERIC_DATE_PATTERN = re.compile(r"\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b")


def compute_content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _visible_text(html: str) -> str:
    """Human-visible text: strip script/style/noscript, then all remaining tags."""
    without_code = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", without_code)
    return re.sub(r"\s+", " ", text).strip()


def _count_date_signals(text: str) -> int:
    return len(_MONTH_NAME_PATTERN.findall(text)) + len(
        _NUMERIC_DATE_PATTERN.findall(text)
    )


def needs_browser_render(html: str) -> bool:
    """True if this HTTP page looks like a JS shell that needs browser rendering."""
    visible = _visible_text(html)
    if len(visible) < _CONTENT_LIGHT_VISIBLE_TEXT_THRESHOLD:
        return True
    return _count_date_signals(visible) < _MIN_DATE_SIGNALS_FOR_LISTING


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _http_get(url: str) -> str:
    """Fetch a URL over plain HTTP with a browser User-Agent. Raises on failure."""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": BROWSER_USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    try:
        with urllib.request.urlopen(
            request, timeout=HTTP_HARD_TIMEOUT_SECONDS
        ) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as error:
        if error.code in _BLOCKING_STATUS_CODES:
            raise _HttpBlocked(error.code) from error
        raise _HttpFailed(f"HTTP {error.code} for {url}") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise _HttpFailed(f"request failed for {url}: {error}") from error


def try_http_get(url: str) -> str | None:
    """Best-effort plain-HTTP GET returning text, or None on any failure.

    Used for Funcheap's secondary per-event page fetches, where a single failed
    page should just be skipped rather than aborting the source.
    """
    try:
        return _http_get(url)
    except (_HttpBlocked, _HttpFailed):
        return None


def _browser_get(url: str) -> str:
    """Fetch a URL's rendered HTML via the Fortress browser helper subprocess."""
    try:
        completed = subprocess.run(
            ["uv", "run", "python", str(BROWSER_FETCH_HELPER_PATH), url],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=BROWSER_HARD_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as error:
        raise _HttpFailed(
            f"browser fetch subprocess failed for {url}: {error}"
        ) from error
    if completed.returncode != 0 or not completed.stdout.strip():
        raise _HttpFailed(
            f"browser fetch returned no content for {url}: "
            f"{completed.stderr.strip()[:300]}"
        )
    return completed.stdout


def _ok_fetch(source: SourceConfig, content: str, *, via_browser: bool) -> RawFetch:
    return RawFetch(
        source_key=source.key,
        source_name=source.name,
        url=source.url,
        status=FetchStatus.OK,
        fetched_at=_now_utc_iso(),
        content=content,
        content_hash=compute_content_hash(content),
        via_browser=via_browser,
    )


def fetch_source(source: SourceConfig) -> RawFetch:
    """Fetch one source, trying HTTP then browser (or browser-first if configured).

    Always returns a RawFetch; a total failure is reported as blocked/error
    rather than raised, so a multi-source run tolerates the loss of one source.
    """
    is_browser_first = source.needs_browser
    was_blocked = False
    last_error = ""
    # A content-light HTTP page (JS shell) is kept as a fallback while we try the
    # browser for the rendered version; used only if the browser then fails too.
    light_http_content: str | None = None

    # Ordered list of (method_name, callable) to try until one succeeds.
    methods = (
        [("browser", _browser_get), ("http", _http_get)]
        if is_browser_first
        else [("http", _http_get), ("browser", _browser_get)]
    )
    for method_name, fetch in methods:
        try:
            content = fetch(source.url)
        except _HttpBlocked as error:
            was_blocked = True
            last_error = str(error)
            print(
                f"[fetch] {source.key}: {method_name} blocked ({error}); trying next",
                file=sys.stderr,
            )
            continue
        except _HttpFailed as error:
            last_error = str(error)
            print(
                f"[fetch] {source.key}: {method_name} failed ({error}); trying next",
                file=sys.stderr,
            )
            continue
        # A JS-shell HTTP response has little text or no dates -- stash it and
        # try the browser for the rendered content instead.
        if method_name == "http" and needs_browser_render(content):
            print(
                f"[fetch] {source.key}: HTTP response is content-light; "
                "trying browser for the rendered page",
                file=sys.stderr,
            )
            light_http_content = content
            continue
        return _ok_fetch(source, content, via_browser=(method_name == "browser"))

    # Browser didn't yield anything better -- fall back to the light HTTP page.
    if light_http_content is not None:
        return _ok_fetch(source, light_http_content, via_browser=False)

    return RawFetch(
        source_key=source.key,
        source_name=source.name,
        url=source.url,
        status=FetchStatus.BLOCKED if was_blocked else FetchStatus.ERROR,
        fetched_at=_now_utc_iso(),
        content=None,
        content_hash=None,
        via_browser=is_browser_first,
        error=last_error or "unknown fetch failure",
    )
