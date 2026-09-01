#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Frozen data types and errors shared across the kid-events-radar pipeline.

This module is the lowest layer: it imports nothing from the other radar
modules, so every stage (fetch, parse, extract, locate, curate, assemble) can
depend on it without a circular import. All application data is expressed as
frozen pydantic models rather than raw dicts.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class KidEventsRadarError(Exception):
    """Base class for every error raised by the kid-events-radar pipeline."""


class SourceConfigError(KidEventsRadarError, ValueError):
    """Raised when the sources config file is missing or malformed."""


class FetchError(KidEventsRadarError, RuntimeError):
    """Raised when a source cannot be fetched by any available method."""


class ParseError(KidEventsRadarError, ValueError):
    """Raised when a source's payload cannot be parsed into candidate events."""


class ParserKind(str, Enum):
    """How a source's payload is turned into candidate events."""

    # Deterministic: scrape the Funcheap listing for per-event permalinks, then
    # read each event page's schema.org JSON-LD. No model call.
    FUNCHEAP = "funcheap"
    # Deterministic feed parse (xml.etree) to pull recent items, then a model
    # extraction over each item's prose (a newsletter lists events in prose).
    AI_RSS = "ai_rss"
    # Irregular HTML: convert to link-preserving markdown, then a model
    # extraction. The fallback for sources with no structured format.
    AI_HTML = "ai_html"


class FetchStatus(str, Enum):
    """Outcome of fetching one source."""

    OK = "ok"
    BLOCKED = "blocked"
    ERROR = "error"


class LocationStatus(str, Enum):
    """Whether an event's venue resolved to coordinates."""

    RESOLVED = "resolved"
    TBD = "tbd"


class LinkQuality(str, Enum):
    """Whether source_url is the event's own page or a fallback landing page."""

    EVENT = "event"
    LANDING = "landing"


class TimeConfidence(str, Enum):
    """How trustworthy an event's start time is.

    HIGH: a single unambiguous time (or agreeing sources). LOW: the source
    contradicts itself (header vs prose) or sources disagree -- both candidate
    times are captured and the UI should hint 'verify time'. UNKNOWN: no time
    was found at all.
    """

    HIGH = "high"
    LOW = "low"
    UNKNOWN = "unknown"


class SourceConfig(BaseModel):
    """One configured event source, loaded from assets/sources.toml."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(description="Stable machine id, e.g. 'funcheap'")
    name: str = Field(description="Display name, e.g. 'SF Funcheap'")
    url: str = Field(description="Listing page or feed URL to fetch")
    parser: ParserKind = Field(description="How to turn the payload into events")
    needs_browser: bool = Field(
        default=False,
        description="Fetch via the Fortress browser rather than plain HTTP",
    )


class PanelEntry(BaseModel):
    """A source that cannot be automated -- shown in the check-yourself panel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Display name of the source")
    url: str = Field(description="Where the user goes to check it themselves")
    why: str = Field(description="Why it can't be pulled automatically")


class RadarSources(BaseModel):
    """The full parsed sources config: automatable sources plus the panel."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sources: tuple[SourceConfig, ...] = Field(description="Automatable sources")
    panel: tuple[PanelEntry, ...] = Field(description="Non-automatable sources")


class RawFetch(BaseModel):
    """The raw payload fetched from one source, plus how the fetch went."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_key: str = Field(description="Which source this came from")
    source_name: str = Field(description="Display name of the source")
    url: str = Field(description="URL that was fetched")
    status: FetchStatus = Field(description="ok / blocked / error")
    fetched_at: str = Field(description="UTC ISO 8601 timestamp of the fetch")
    content: str | None = Field(
        default=None, description="Raw text payload (HTML/XML); None on failure"
    )
    content_hash: str | None = Field(
        default=None, description="sha256 of the content, for incremental skipping"
    )
    via_browser: bool = Field(
        default=False, description="Whether the Fortress browser was used"
    )
    error: str | None = Field(default=None, description="Error detail on failure")


class Event(BaseModel):
    """A single kid event, accumulating fields as it moves through the pipeline.

    Parsers/extraction populate the descriptive fields; ``locate`` fills the
    geo fields; ``curate`` fills kid_fit / flag / age_is_appropriate /
    event_type. Optional fields are None until the relevant stage runs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Provenance and descriptive fields (from parse / extract)
    source_key: str = Field(description="Machine id of the source")
    source_name: str = Field(description="Display name of the source")
    title: str = Field(description="Event title")
    date: str | None = Field(
        default=None, description="Start date as YYYY-MM-DD, or None if unknown"
    )
    end_date: str | None = Field(
        default=None, description="End date as YYYY-MM-DD for multi-day events"
    )
    day: str | None = Field(default=None, description="Weekday label, e.g. 'Saturday'")
    start_time: str | None = Field(
        default=None, description="Human start time, e.g. '10:00 AM', or None"
    )
    time_confidence: TimeConfidence = Field(
        default=TimeConfidence.UNKNOWN,
        description="Trust level of start_time (high / low / unknown)",
    )
    time_candidates: dict[str, str] = Field(
        default_factory=dict,
        description="Conflicting candidate times by label when time_confidence is low",
    )
    venue: str | None = Field(default=None, description="Venue name")
    city: str | None = Field(default=None, description="City")
    address: str | None = Field(
        default=None, description="Street address if available (best for geocoding)"
    )
    price_text: str | None = Field(
        default=None, description="Raw price note, e.g. 'FREE' or '$44+'"
    )
    source_url: str = Field(description="Deep link to the event's own page")
    link_quality: LinkQuality = Field(
        description="Whether source_url is the event page or a landing fallback"
    )
    raw_excerpt: str = Field(description="Short raw snippet from the source")

    # Geo fields (from locate)
    venue_latitude: float | None = Field(default=None, description="Venue latitude")
    venue_longitude: float | None = Field(default=None, description="Venue longitude")
    drive_minutes: int | None = Field(
        default=None, description="Estimated drive minutes from home; None if TBD"
    )
    location_status: LocationStatus = Field(
        default=LocationStatus.TBD, description="Whether the venue resolved"
    )
    area: str | None = Field(
        default=None, description="Coarse region, e.g. 'SF' / 'Marin' / 'South Bay'"
    )

    # Curation fields (from curate)
    kid_fit: str | None = Field(
        default=None, description="One-line note on fit for the age range"
    )
    flag: str | None = Field(
        default=None,
        description="Short caveat badge (paid/registration/fundraiser), else None",
    )
    event_type: str | None = Field(
        default=None, description="Coarse type, e.g. 'festival' / 'market' / 'film'"
    )
    age_is_appropriate: bool | None = Field(
        default=None,
        description="Whether curate judged it kid-attendable; None until curated",
    )

    # Ranking transparency (from assemble, only when a preference profile applies)
    de_emphasis_penalty_minutes: int = Field(
        default=0,
        description="Minutes added to the sort key by disliked-attribute matches",
    )
    de_emphasis_reasons: tuple[str, ...] = Field(
        default=(), description="Which disliked attributes matched, for transparency"
    )


class SourceStatus(BaseModel):
    """Per-source outcome summary, surfaced in the output metadata."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(description="Source machine id")
    name: str = Field(description="Source display name")
    status: FetchStatus = Field(description="Fetch outcome")
    event_count: int = Field(description="Events contributed after parsing")
    from_cache: bool = Field(
        default=False, description="Whether the parse was reused from the cache"
    )
    via_browser: bool = Field(default=False, description="Whether the browser was used")
    error: str | None = Field(default=None, description="Error detail if any")


class WeekendGroup(BaseModel):
    """One weekend's worth of events, for a viewer that pages forward by weekend."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    weekend_of: str = Field(description="The Saturday date of the weekend, YYYY-MM-DD")
    label: str = Field(description="Human label, e.g. 'Aug 22-23'")
    events: tuple[Event, ...] = Field(description="Events that weekend, ranked")


class RadarOutput(BaseModel):
    """The full radar snapshot written to the stable latest.json path."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    generated_for: str = Field(description="Home description")
    home_address: str = Field(description="Configured home address")
    home_latitude: float = Field(description="Home latitude")
    home_longitude: float = Field(description="Home longitude")
    today: str = Field(description="The date the run treats as today, YYYY-MM-DD")
    fetched_at: str = Field(description="UTC ISO 8601 timestamp the snapshot was built")
    age_filter: str = Field(description="Age range, e.g. '4-7'")
    drive_radius: str = Field(description="Human radius description")
    drive_radius_minutes: int = Field(description="Radius in minutes")
    window_start: str = Field(description="First date in the pull window")
    window_end: str = Field(description="Last date in the pull window")
    weeks_ahead: int = Field(description="How many weeks ahead the window spans")
    sources_pulled: tuple[str, ...] = Field(description="Display names pulled")
    source_status: tuple[SourceStatus, ...] = Field(description="Per-source outcomes")
    weekends: tuple[WeekendGroup, ...] = Field(description="Weekend events, grouped")
    thu_fri_evening: tuple[Event, ...] = Field(
        description="Thursday/Friday early-evening events, ranked"
    )
    check_yourself_panel: tuple[PanelEntry, ...] = Field(
        description="Sources the user must check manually"
    )
    preference_profile_applied: bool = Field(
        default=False, description="Whether a preference profile adjusted the ranking"
    )


class PreferenceProfile(BaseModel):
    """A transparent, inspectable set of weighted dislikes by event attribute.

    Each inner mapping is attribute-value -> dislike weight (a positive float;
    larger means 'show fewer like this' more strongly). The assemble step turns
    a matching event's summed weights into a minutes penalty that sinks it in
    the sort order without removing it. Absent this profile, ranking is pure
    drive-time ascending.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: int = Field(default=1, description="Profile schema version")
    updated_at: str | None = Field(
        default=None, description="UTC ISO 8601 of last edit"
    )
    source: dict[str, float] = Field(
        default_factory=dict, description="Disliked source display names -> weight"
    )
    city: dict[str, float] = Field(
        default_factory=dict, description="Disliked cities -> weight"
    )
    area: dict[str, float] = Field(
        default_factory=dict, description="Disliked coarse regions -> weight"
    )
    venue: dict[str, float] = Field(
        default_factory=dict, description="Disliked venues -> weight"
    )
    title_keyword: dict[str, float] = Field(
        default_factory=dict, description="Disliked lowercased title keywords -> weight"
    )
    drive_bucket: dict[str, float] = Field(
        default_factory=dict,
        description="Disliked drive buckets (e.g. '45-60') -> weight",
    )
    event_type: dict[str, float] = Field(
        default_factory=dict, description="Disliked event types -> weight"
    )
    notes: tuple[str, ...] = Field(
        default=(), description="Free-text provenance notes (e.g. chat instructions)"
    )
