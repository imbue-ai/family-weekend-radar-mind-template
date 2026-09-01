#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Geocode venues (Nominatim) and compute drive time from home (OSRM).

Both services are free and need no API key. Results are cached by the caller so
each unique venue is geocoded at most once across runs; live Nominatim calls are
rate-limited to one per second per its usage policy.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
_OSRM_URL = "https://router.project-osrm.org/route/v1/driving"
_GEOCODE_USER_AGENT = "kid-events-radar/1.0 (family event radar; contact via workspace)"
_NETWORK_TIMEOUT_SECONDS = 30.0
_NOMINATIM_MIN_INTERVAL_SECONDS = 1.0

# Coarse Bay Area regions, so the preference profile can de-emphasize by area.
_AREA_BY_CITY_KEYWORD: tuple[tuple[str, str], ...] = (
    ("san francisco", "SF"),
    ("sausalito", "Marin"),
    ("san rafael", "Marin"),
    ("mill valley", "Marin"),
    ("novato", "Marin"),
    ("larkspur", "Marin"),
    ("corte madera", "Marin"),
    ("tiburon", "Marin"),
    ("san anselmo", "Marin"),
    ("fairfax", "Marin"),
    ("oakland", "East Bay"),
    ("berkeley", "East Bay"),
    ("alameda", "East Bay"),
    ("emeryville", "East Bay"),
    ("richmond", "East Bay"),
    ("el cerrito", "East Bay"),
    ("san ramon", "East Bay"),
    ("walnut creek", "East Bay"),
    ("piedmont", "East Bay"),
    ("fremont", "East Bay"),
    ("hayward", "East Bay"),
    ("pleasant hill", "East Bay"),
    ("daly city", "Peninsula"),
    ("south san francisco", "Peninsula"),
    ("san mateo", "Peninsula"),
    ("millbrae", "Peninsula"),
    ("burlingame", "Peninsula"),
    ("redwood city", "Peninsula"),
    ("menlo park", "Peninsula"),
    ("palo alto", "Peninsula"),
    ("mountain view", "Peninsula"),
    ("san jose", "South Bay"),
    ("santa clara", "South Bay"),
    ("sunnyvale", "South Bay"),
    ("campbell", "South Bay"),
    ("cupertino", "South Bay"),
)


def derive_area(city: str | None) -> str | None:
    if not city:
        return None
    lowered = city.lower()
    for keyword, area in _AREA_BY_CITY_KEYWORD:
        if keyword in lowered:
            return area
    return None


def canonical_city(*candidates: str | None) -> str | None:
    """Return a clean, canonical Bay Area city name found in any candidate text.

    Funcheap's JSON-LD address is sometimes a bare street string, so the parsed
    ``city`` can carry a street number. Scanning the known-city keywords against
    the address/venue/city recovers a clean city name for display, area
    derivation, and preference matching. Longer keywords win (so 'south san
    francisco' beats 'san francisco').
    """
    combined = " ".join(part for part in candidates if part).lower()
    if not combined:
        return None
    best_keyword = ""
    for keyword, _area in _AREA_BY_CITY_KEYWORD:
        if keyword in combined and len(keyword) > len(best_keyword):
            best_keyword = keyword
    return best_keyword.title() if best_keyword else None


def geocode(query: str) -> tuple[float, float] | None:
    """Look up coordinates for a location string via Nominatim. None if not found."""
    encoded = urllib.parse.urlencode(
        {"q": query, "format": "json", "limit": 1, "countrycodes": "us"}
    )
    request = urllib.request.Request(
        f"{_NOMINATIM_URL}?{encoded}", headers={"User-Agent": _GEOCODE_USER_AGENT}
    )
    try:
        with urllib.request.urlopen(
            request, timeout=_NETWORK_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        print(f"[locate] geocode failed for {query!r}: {error}", file=sys.stderr)
        return None
    finally:
        # Respect Nominatim's 1 req/s policy on every live call.
        time.sleep(_NOMINATIM_MIN_INTERVAL_SECONDS)
    if not isinstance(payload, list) or not payload:
        return None
    first = payload[0]
    try:
        return (float(first["lat"]), float(first["lon"]))
    except (KeyError, TypeError, ValueError):
        return None


def compute_drive_minutes(
    home: tuple[float, float], destination: tuple[float, float]
) -> int | None:
    """Estimate driving minutes from home to destination via OSRM. None on failure."""
    coordinates = f"{home[1]},{home[0]};{destination[1]},{destination[0]}"
    url = f"{_OSRM_URL}/{coordinates}?overview=false"
    request = urllib.request.Request(url, headers={"User-Agent": _GEOCODE_USER_AGENT})
    try:
        with urllib.request.urlopen(
            request, timeout=_NETWORK_TIMEOUT_SECONDS
        ) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        print(f"[locate] routing failed: {error}", file=sys.stderr)
        return None
    routes = payload.get("routes") if isinstance(payload, dict) else None
    if not routes:
        return None
    duration_seconds = routes[0].get("duration")
    if not isinstance(duration_seconds, (int, float)):
        return None
    return max(1, round(duration_seconds / 60.0))
