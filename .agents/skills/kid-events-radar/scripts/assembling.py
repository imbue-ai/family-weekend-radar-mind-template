#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Pure assembly: dedupe, freshness/radius filter, bucket, rank, build the snapshot.

Ranking is drive-time ascending by default. An optional preference profile adds
a transparent per-event minutes penalty (summed weights of matching disliked
attributes) that sinks de-emphasized events toward the bottom of each bucket
without removing them. Absent a profile, ranking is pure drive-time.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from difflib import SequenceMatcher
from typing import Final

from radar_dates import (
    is_thursday_or_friday,
    is_weekend,
    parse_iso_date,
    saturday_of_weekend,
    weekend_label,
)
from radar_types import (
    Event,
    LinkQuality,
    LocationStatus,
    PanelEntry,
    PreferenceProfile,
    RadarOutput,
    SourceStatus,
    WeekendGroup,
)

# Each unit of summed dislike weight sinks an event by this many "virtual"
# drive-minutes in the sort order. Transparent and inspectable by design.
PENALTY_MINUTES_PER_WEIGHT: Final[int] = 20
# Sort key stand-in for a location-TBD event (no drive time) so it sinks last.
_UNKNOWN_DRIVE_SORT_MINUTES: Final[int] = 10_000
_MULTI_DAY_SCAN_LIMIT_DAYS: Final[int] = 60
# Fuzzy-dedup title-similarity threshold for the ratio branch (only used when the
# two events also share a contained venue, so it can afford to be moderate).
_TITLE_SIMILARITY_THRESHOLD: Final[float] = 0.62


def _normalized_title(title: str) -> str:
    """Normalize a title for duplicate matching.

    Strips leading ordinals ("8th Annual"/"44th Annual"), parenthetical
    suffixes (usually a location like "(Alameda)"), possessives, and
    punctuation, then lowercases and collapses whitespace -- so the same event
    listed slightly differently across two sources normalizes to a comparable
    string.
    """
    lowered = title.lower()
    lowered = re.sub(r"\([^)]*\)", " ", lowered)
    lowered = re.sub(r"\b\d+\s*(?:st|nd|rd|th)\s+annual\b", " ", lowered)
    lowered = re.sub(r"[’']s\b", " ", lowered)
    return re.sub(r"[^a-z0-9]+", " ", lowered).strip()


def _normalized_place(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip() if value else ""


def _one_contains_other(left: str, right: str) -> bool:
    return bool(left) and bool(right) and (left in right or right in left)


def _venue_city_compatible(a: Event, b: Event) -> bool:
    """True when two events' venue/city do not contradict.

    A side that lacks both venue and city can't contradict (some sources omit
    location). Otherwise the venues or the cities must be the same or one
    contained in the other.
    """
    a_venue, a_city = _normalized_place(a.venue), _normalized_place(a.city)
    b_venue, b_city = _normalized_place(b.venue), _normalized_place(b.city)
    if (not a_venue and not a_city) or (not b_venue and not b_city):
        return True
    return _one_contains_other(a_venue, b_venue) or _one_contains_other(a_city, b_city)


def _venues_agree_or_absent(a: Event, b: Event) -> bool:
    """True when the venues corroborate a match rather than contradict it.

    One venue contains the other, or at least one side omits its venue (so it
    cannot contradict). A same-city-but-different-venue pair does NOT qualify --
    that is the signal that keeps two genuinely different same-day events in the
    same city from being merged on a partial (non-exact) title match.
    """
    a_venue, b_venue = _normalized_place(a.venue), _normalized_place(b.venue)
    if not a_venue or not b_venue:
        return True
    return _one_contains_other(a_venue, b_venue)


def _titles_are_duplicate(a: Event, b: Event) -> bool:
    """Fuzzy title match: equal, token-subset, or high similarity + shared venue."""
    a_norm, b_norm = _normalized_title(a.title), _normalized_title(b.title)
    if not a_norm or not b_norm:
        return False
    if a_norm == b_norm:
        return True
    a_tokens, b_tokens = set(a_norm.split()), set(b_norm.split())
    smaller, larger = (
        (a_tokens, b_tokens) if len(a_tokens) <= len(b_tokens) else (b_tokens, a_tokens)
    )
    # A token-subset match is only a duplicate when the venues also corroborate
    # it (or one is absent); a shared city alone is too weak, since many distinct
    # same-day events sit in the same city.
    if len(smaller) >= 2 and smaller <= larger and _venues_agree_or_absent(a, b):
        return True
    # A moderate string similarity only counts when the venues also line up, to
    # avoid merging two genuinely different same-day events with similar wording.
    if _one_contains_other(_normalized_place(a.venue), _normalized_place(b.venue)):
        return SequenceMatcher(None, a_norm, b_norm).ratio() >= _TITLE_SIMILARITY_THRESHOLD
    return False


def _is_duplicate(a: Event, b: Event) -> bool:
    """Two events are the same when their dates match, their venue/city are
    compatible, and their normalized titles are a fuzzy match."""
    if a.date is None or b.date is None or a.date != b.date:
        return False
    if not _venue_city_compatible(a, b):
        return False
    return _titles_are_duplicate(a, b)


def _event_quality(event: Event) -> tuple[int, int, int]:
    """Higher is better -- used to pick the best duplicate to keep."""
    return (
        1 if event.link_quality == LinkQuality.EVENT else 0,
        1 if event.location_status == LocationStatus.RESOLVED else 0,
        1 if event.kid_fit else 0,
    )


def dedupe_events(events: list[Event]) -> list[Event]:
    """Collapse duplicate events across sources, keeping the best-data record.

    Duplicates are matched fuzzily -- same date, compatible venue/city, and a
    normalized/fuzzy title match -- so the same event listed under slightly
    different titles by two sources collapses to one. The kept record is the one
    with the better data (a real per-event deep link, a resolved location, a fit
    note), per ``_event_quality``.
    """
    kept: list[Event] = []
    for event in events:
        match_index = next(
            (index for index, other in enumerate(kept) if _is_duplicate(event, other)),
            None,
        )
        if match_index is None:
            kept.append(event)
        elif _event_quality(event) > _event_quality(kept[match_index]):
            kept[match_index] = event
    return kept


def drive_bucket(drive_minutes: int | None) -> str:
    if drive_minutes is None:
        return "unknown"
    if drive_minutes <= 15:
        return "0-15"
    if drive_minutes <= 30:
        return "15-30"
    if drive_minutes <= 45:
        return "30-45"
    if drive_minutes <= 60:
        return "45-60"
    return "60+"


def _covered_dates(event: Event, today: date, window_end: date) -> list[date]:
    start = parse_iso_date(event.date) if event.date else None
    if start is None:
        return []
    end = parse_iso_date(event.end_date) if event.end_date else start
    if end is None or end < start:
        end = start
    covered: list[date] = []
    cursor = max(start, today)
    last = min(end, window_end, start + timedelta(days=_MULTI_DAY_SCAN_LIMIT_DAYS))
    while cursor <= last:
        covered.append(cursor)
        cursor = cursor + timedelta(days=1)
    return covered


def bucket_for_event(
    event: Event, today: date, window_end: date
) -> tuple[str, date | None] | None:
    """Classify an event as ('weekend', saturday) / ('evening', None), else None."""
    covered = _covered_dates(event, today, window_end)
    if not covered:
        return None
    weekend_days = [day for day in covered if is_weekend(day)]
    if weekend_days:
        return ("weekend", saturday_of_weekend(min(weekend_days)))
    if any(is_thursday_or_friday(day) for day in covered):
        return ("evening", None)
    return None


def is_within_radius(event: Event, radius_minutes: int) -> bool:
    """A located event must be within radius; a TBD event is kept (shown, ranked last)."""
    if event.drive_minutes is None:
        return True
    return event.drive_minutes <= radius_minutes


def _match_weight(profile_map: dict[str, float], value: str | None) -> float:
    if not value:
        return 0.0
    lowered = value.lower()
    for disliked, weight in profile_map.items():
        if disliked.lower() == lowered:
            return float(weight)
    return 0.0


def compute_de_emphasis(
    event: Event, profile: PreferenceProfile
) -> tuple[int, tuple[str, ...]]:
    """Return (penalty_minutes, reasons) from the event's disliked attributes."""
    contributions: list[tuple[str, float]] = []
    contributions.append(("source", _match_weight(profile.source, event.source_name)))
    contributions.append(("city", _match_weight(profile.city, event.city)))
    contributions.append(("area", _match_weight(profile.area, event.area)))
    contributions.append(("venue", _match_weight(profile.venue, event.venue)))
    contributions.append(
        ("event_type", _match_weight(profile.event_type, event.event_type))
    )
    contributions.append(
        (
            "drive_bucket",
            _match_weight(profile.drive_bucket, drive_bucket(event.drive_minutes)),
        )
    )
    title_lower = event.title.lower()
    keyword_weight = sum(
        weight
        for keyword, weight in profile.title_keyword.items()
        if keyword.lower() in title_lower
    )
    contributions.append(("title_keyword", keyword_weight))

    total_weight = sum(weight for _, weight in contributions)
    reasons = tuple(
        f"{name}(+{weight:g})" for name, weight in contributions if weight > 0
    )
    return round(total_weight * PENALTY_MINUTES_PER_WEIGHT), reasons


def _apply_de_emphasis(event: Event, profile: PreferenceProfile | None) -> Event:
    if profile is None:
        return event
    penalty, reasons = compute_de_emphasis(event, profile)
    if penalty == 0 and not reasons:
        return event
    return event.model_copy(
        update={"de_emphasis_penalty_minutes": penalty, "de_emphasis_reasons": reasons}
    )


def _sort_key(event: Event) -> tuple[int, str, str]:
    base = (
        event.drive_minutes
        if event.drive_minutes is not None
        else _UNKNOWN_DRIVE_SORT_MINUTES
    )
    return (
        base + event.de_emphasis_penalty_minutes,
        event.date or "9999-99-99",
        event.title.lower(),
    )


def rank_events(events: list[Event], profile: PreferenceProfile | None) -> list[Event]:
    """Apply the preference de-emphasis, then sort by adjusted drive time."""
    adjusted = [_apply_de_emphasis(event, profile) for event in events]
    return sorted(adjusted, key=_sort_key)


def group_into_weekends(
    weekend_events: list[tuple[date, Event]], profile: PreferenceProfile | None
) -> list[WeekendGroup]:
    events_by_saturday: dict[date, list[Event]] = {}
    for saturday, event in weekend_events:
        events_by_saturday.setdefault(saturday, []).append(event)
    groups: list[WeekendGroup] = []
    for saturday in sorted(events_by_saturday):
        ranked = rank_events(events_by_saturday[saturday], profile)
        groups.append(
            WeekendGroup(
                weekend_of=saturday.isoformat(),
                label=weekend_label(saturday),
                events=tuple(ranked),
            )
        )
    return groups


def build_radar_output(
    *,
    events: list[Event],
    today: date,
    window_end: date,
    weeks_ahead: int,
    radius_minutes: int,
    home_description: str,
    home_address: str,
    home_latitude: float,
    home_longitude: float,
    age_min: int,
    age_max: int,
    sources_pulled: tuple[str, ...],
    source_status: tuple[SourceStatus, ...],
    panel: tuple[PanelEntry, ...],
    fetched_at: str,
    profile: PreferenceProfile | None,
) -> RadarOutput:
    """Dedupe, filter, bucket, rank, and assemble the full radar snapshot."""
    deduped = dedupe_events(events)
    within_radius = [
        event for event in deduped if is_within_radius(event, radius_minutes)
    ]

    weekend_events: list[tuple[date, Event]] = []
    evening_events: list[Event] = []
    for event in within_radius:
        bucket = bucket_for_event(event, today, window_end)
        if bucket is None:
            continue
        kind, saturday = bucket
        if kind == "weekend" and saturday is not None:
            weekend_events.append((saturday, event))
        elif kind == "evening":
            evening_events.append(event)

    weekends = group_into_weekends(weekend_events, profile)
    evenings = rank_events(evening_events, profile)

    return RadarOutput(
        generated_for=home_description,
        home_address=home_address,
        home_latitude=home_latitude,
        home_longitude=home_longitude,
        today=today.isoformat(),
        fetched_at=fetched_at,
        age_filter=f"{age_min}-{age_max}",
        drive_radius=f"{radius_minutes} minutes (intentionally wide to reach beyond the city)",
        drive_radius_minutes=radius_minutes,
        window_start=today.isoformat(),
        window_end=window_end.isoformat(),
        weeks_ahead=weeks_ahead,
        sources_pulled=sources_pulled,
        source_status=source_status,
        weekends=tuple(weekends),
        thu_fri_evening=tuple(evenings),
        check_yourself_panel=panel,
        preference_profile_applied=profile is not None,
    )
