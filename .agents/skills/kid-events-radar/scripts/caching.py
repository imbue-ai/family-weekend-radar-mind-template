#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Incremental caches: a content-hash parse cache and a geocode cache.

The parse cache is the main cost saver: if a source's fetched content hashes to
the same value as last run, its already-parsed events (including any model
extraction) are reused instead of re-parsing/re-calling the model. Both caches
are bounded so they cannot grow without limit.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import ValidationError
from radar_types import Event

_PARSE_CACHE_MAX_AGE_DAYS = 45
_GEOCACHE_MAX_ENTRIES = 5000


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_json_object(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A corrupt cache is not fatal -- warn and start fresh (it only costs a
        # re-fetch/re-parse), rather than crashing the whole run.
        print(f"[cache] warning: could not read cache at {path}; ignoring it")
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp_path.replace(path)


# --- Parse cache -----------------------------------------------------------


def load_parse_cache(path: Path) -> dict:
    cache = _load_json_object(path)
    return cache if "entries" in cache else {"version": 1, "entries": {}}


def lookup_cached_events(
    cache: dict, url: str, content_hash: str
) -> list[Event] | None:
    """Return cached events for a URL iff the content hash matches, else None."""
    entry = cache.get("entries", {}).get(url)
    if not isinstance(entry, dict) or entry.get("content_hash") != content_hash:
        return None
    try:
        return [Event.model_validate(item) for item in entry.get("events", [])]
    except ValidationError:
        return None


def store_cached_events(
    cache: dict, url: str, content_hash: str, events: list[Event]
) -> dict:
    """Return a new cache dict with this URL's parsed events recorded."""
    entries = dict(cache.get("entries", {}))
    entries[url] = {
        "content_hash": content_hash,
        "cached_at": _now_utc_iso(),
        "events": [event.model_dump(mode="json") for event in events],
    }
    return {"version": 1, "entries": entries}


def prune_parse_cache(cache: dict) -> dict:
    """Drop entries older than the max age so the cache cannot grow unbounded."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=_PARSE_CACHE_MAX_AGE_DAYS)
    kept: dict = {}
    for url, entry in cache.get("entries", {}).items():
        cached_at = entry.get("cached_at") if isinstance(entry, dict) else None
        if not isinstance(cached_at, str):
            continue
        try:
            stamp = datetime.fromisoformat(cached_at)
        except ValueError:
            continue
        if stamp >= cutoff:
            kept[url] = entry
    return {"version": 1, "entries": kept}


def save_parse_cache(path: Path, cache: dict) -> None:
    _atomic_write_json(path, prune_parse_cache(cache))


# --- Geocode cache ---------------------------------------------------------


def geocode_key(query: str) -> str:
    return " ".join(query.lower().split())


def load_geocache(path: Path) -> dict:
    cache = _load_json_object(path)
    return cache if "entries" in cache else {"version": 1, "entries": {}}


def lookup_geocode(cache: dict, query: str) -> tuple[float, float] | None | str:
    """Return coords, None (cached-as-unresolvable), or 'miss' if not cached.

    A cached ``null`` result is meaningful ("we already tried and failed"), so
    it is distinguished from a cache miss to avoid re-querying known failures.
    """
    entry = cache.get("entries", {}).get(geocode_key(query))
    if entry is None:
        return "miss"
    if not isinstance(entry, dict):
        return "miss"
    latitude = entry.get("lat")
    longitude = entry.get("lon")
    if latitude is None or longitude is None:
        return None
    return (float(latitude), float(longitude))


def store_geocode(cache: dict, query: str, coords: tuple[float, float] | None) -> dict:
    entries = dict(cache.get("entries", {}))
    entries[geocode_key(query)] = (
        {"lat": None, "lon": None}
        if coords is None
        else {"lat": coords[0], "lon": coords[1]}
    )
    # Bound the geocache: if it exceeds the cap, keep only the most recent half
    # (dict preserves insertion order, so slice off the oldest entries).
    if len(entries) > _GEOCACHE_MAX_ENTRIES:
        recent_items = list(entries.items())[-(_GEOCACHE_MAX_ENTRIES // 2) :]
        entries = dict(recent_items)
    return {"version": 1, "entries": entries}


def save_geocache(path: Path, cache: dict) -> None:
    _atomic_write_json(path, cache)
