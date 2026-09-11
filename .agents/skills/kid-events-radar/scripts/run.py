#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""kid-events-radar pipeline entry point.

Subcommands, one per pipeline stage, plus a headless ``run all`` that chains
them in-process:

  fetch     -- fetch each source's page/feed (browser fallback for blockers)
  extract   -- parse structured sources deterministically; model-extract the
               irregular ones; reuse unchanged sources from the content-hash cache
  locate    -- geocode venues and compute drive time from home
  curate    -- verify kid-attendability, add a fit note, flag caveats
  assemble  -- dedupe, filter, bucket by weekend, rank, write latest.json
  run all   -- all of the above, then update the stable snapshot

Plus a standalone helper used when adopting the skill for a new area:

  verify-source -- fetch ONE candidate URL (with the browser fallback), run the
                   event extraction over it, and print a verdict: how many dated,
                   kid-appropriate events it yields in the window, a few examples,
                   the parse ``type`` to put in sources.toml, and whether it needs
                   the browser. Exits 0 on a usable source, non-zero otherwise.

Every stage reads/writes serializable JSON under a run directory, so a chat can
drive the stages one at a time; ``run all`` skips the serialization. All model
calls use the keyless ``claude -p`` path.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from assembling import build_radar_output
from caching import (
    load_geocache,
    load_parse_cache,
    lookup_cached_events,
    lookup_geocode,
    save_geocache,
    save_parse_cache,
    store_cached_events,
    store_geocode,
)
from curating import curate_events, is_clearly_not_kid_event
from extraction import extract_events
from fetching import fetch_source, try_http_get
from locating import canonical_city, compute_drive_minutes, derive_area, geocode
from parsing import parse_funcheap_event, parse_funcheap_permalinks
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from radar_config import (
    DEFAULT_AGE_MAX,
    DEFAULT_AGE_MIN,
    DEFAULT_DRIVE_RADIUS_MINUTES,
    DEFAULT_HOME_ADDRESS,
    DEFAULT_HOME_DESCRIPTION,
    DEFAULT_HOME_LATITUDE,
    DEFAULT_HOME_LONGITUDE,
    DEFAULT_MODEL,
    DEFAULT_SOURCES_CONFIG_PATH,
    DEFAULT_WEEKS_AHEAD,
    GEOCACHE_PATH,
    LATEST_SNAPSHOT_PATH,
    PARSE_CACHE_PATH,
    PREFERENCE_PROFILE_PATH,
    RUN_RETENTION_COUNT,
    RUNS_DIR,
    load_sources_config,
)
from radar_dates import compute_window_end, parse_iso_date
from radar_types import (
    Event,
    FetchStatus,
    KidEventsRadarError,
    LocationStatus,
    ParserKind,
    PreferenceProfile,
    RadarSources,
    RawFetch,
    SourceConfig,
    SourceStatus,
)

_FUNCHEAP_EVENT_FETCH_WORKERS = 6
_SOURCE_FETCH_WORKERS = 6
# Concurrency for the per-source parse/extract stage. The keyless model calls
# dominate here, so running the irregular sources in parallel is a large win;
# bounded to keep the claude -p subscription pool and the sites from throttling.
_EXTRACT_WORKERS = 5


class RadarRunConfig(BaseModel):
    """All parameters for one radar run, resolved from CLI args + defaults."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    home_address: str
    home_description: str
    home_latitude: float
    home_longitude: float
    age_min: int
    age_max: int
    drive_radius_minutes: int
    weeks_ahead: int
    model: str
    today: str

    @property
    def today_date(self) -> date:
        parsed = parse_iso_date(self.today)
        if parsed is None:
            raise KidEventsRadarError(f"invalid --today value: {self.today}")
        return parsed

    @property
    def home_coords(self) -> tuple[float, float]:
        return (self.home_latitude, self.home_longitude)


# --- serialization helpers -------------------------------------------------


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_events(path: Path, events: list[Event]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([event.model_dump(mode="json") for event in events], indent=2),
        encoding="utf-8",
    )


def _read_events(path: Path) -> list[Event]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [Event.model_validate(item) for item in raw]


def _write_statuses(path: Path, statuses: list[SourceStatus]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([status.model_dump(mode="json") for status in statuses], indent=2),
        encoding="utf-8",
    )


def _read_statuses(path: Path) -> list[SourceStatus]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [SourceStatus.model_validate(item) for item in raw]


def _extension_for(source: SourceConfig) -> str:
    return "xml" if source.parser == ParserKind.AI_RSS else "html"


# --- Stage: fetch ----------------------------------------------------------


def do_fetch(sources: RadarSources) -> list[RawFetch]:
    """Fetch every source concurrently (bounded), tolerating individual failures."""
    with ThreadPoolExecutor(max_workers=_SOURCE_FETCH_WORKERS) as executor:
        results = list(executor.map(fetch_source, sources.sources))
    return results


def _save_raw_payloads(
    run_dir: Path, sources: RadarSources, fetches: list[RawFetch]
) -> None:
    """Persist each source's raw payload durably (preserve-and-surface)."""
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    source_by_key = {source.key: source for source in sources.sources}
    for fetch in fetches:
        if fetch.content is None:
            continue
        extension = _extension_for(source_by_key[fetch.source_key])
        (raw_dir / f"{fetch.source_key}.{extension}").write_text(
            fetch.content, encoding="utf-8"
        )


# --- Stage: extract --------------------------------------------------------


def _extract_funcheap(source: SourceConfig, listing_html: str) -> list[Event]:
    permalinks = parse_funcheap_permalinks(listing_html, source.url)
    with ThreadPoolExecutor(max_workers=_FUNCHEAP_EVENT_FETCH_WORKERS) as executor:
        pages = list(executor.map(try_http_get, permalinks))
    events: list[Event] = []
    for url, html in zip(permalinks, pages):
        if not html:
            continue
        event = parse_funcheap_event(html, url, source)
        if event is not None:
            events.append(event)
    return events


def _parse_source(
    source: SourceConfig, fetch: RawFetch, config: RadarRunConfig
) -> tuple[list[Event], float]:
    """Turn one fetched source into candidate events; return (events, model_cost)."""
    content = fetch.content or ""
    window_end = compute_window_end(config.today_date, config.weeks_ahead)
    match source.parser:
        case ParserKind.FUNCHEAP:
            return _extract_funcheap(source, content), 0.0
        case ParserKind.AI_HTML | ParserKind.AI_RSS:
            result = extract_events(
                content,
                source,
                today=config.today_date,
                window_end=window_end,
                age_min=config.age_min,
                age_max=config.age_max,
                model=config.model,
            )
            return list(result.events), result.cost_usd
        case _:
            raise KidEventsRadarError(f"unhandled parser kind: {source.parser}")


def _within_window(event: Event, today: date, window_end: date) -> bool:
    start = parse_iso_date(event.date) if event.date else None
    if start is None:
        return False
    end = parse_iso_date(event.end_date) if event.end_date else start
    effective_end = end if (end and end >= start) else start
    return effective_end >= today and start <= window_end


class _ParseOutcome(BaseModel):
    """Result of parsing one non-cached source (may hold an error instead)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_key: str
    events: tuple[Event, ...]
    cost_usd: float
    error: str | None


def _parse_one(
    source: SourceConfig, fetch: RawFetch, config: RadarRunConfig
) -> _ParseOutcome:
    try:
        parsed, cost = _parse_source(source, fetch, config)
    except KidEventsRadarError as error:
        print(f"[extract] {source.key} failed: {error}", file=sys.stderr)
        return _ParseOutcome(
            source_key=source.key, events=(), cost_usd=0.0, error=str(error)
        )
    return _ParseOutcome(
        source_key=source.key, events=tuple(parsed), cost_usd=cost, error=None
    )


def do_extract(
    config: RadarRunConfig,
    sources: RadarSources,
    fetches: list[RawFetch],
    parse_cache: dict,
) -> tuple[list[Event], list[SourceStatus], dict, float]:
    """Parse/extract candidate events, reusing the content-hash cache when unchanged.

    Cache hits and failed fetches are handled inline; the remaining sources are
    parsed/extracted concurrently (independent I/O -- the model calls dominate),
    then folded back into the cache in source order for deterministic output.
    """
    source_by_key = {source.key: source for source in sources.sources}
    today = config.today_date
    window_end = compute_window_end(today, config.weeks_ahead)

    # First pass (sequential, cheap): resolve cache hits and fetch failures, and
    # collect the sources that still need a live parse/extract.
    cached_events_by_key: dict[str, list[Event]] = {}
    failed_status_by_key: dict[str, FetchStatus] = {}
    error_by_key: dict[str, str | None] = {}
    to_parse: list[tuple[SourceConfig, RawFetch]] = []
    for fetch in fetches:
        source = source_by_key[fetch.source_key]
        if (
            fetch.status != FetchStatus.OK
            or fetch.content is None
            or fetch.content_hash is None
        ):
            failed_status_by_key[source.key] = fetch.status
            error_by_key[source.key] = fetch.error
            continue
        cached = lookup_cached_events(parse_cache, fetch.url, fetch.content_hash)
        if cached is not None:
            cached_events_by_key[source.key] = cached
        else:
            to_parse.append((source, fetch))

    # Second pass (concurrent): parse/extract the sources that changed.
    with ThreadPoolExecutor(max_workers=_EXTRACT_WORKERS) as executor:
        outcomes = list(
            executor.map(lambda pair: _parse_one(pair[0], pair[1], config), to_parse)
        )
    outcome_by_key = {outcome.source_key: outcome for outcome in outcomes}
    fetch_by_key = {fetch.source_key: fetch for fetch in fetches}

    # Fold results back together in source order.
    all_events: list[Event] = []
    statuses: list[SourceStatus] = []
    working_cache = parse_cache
    total_cost = 0.0
    for fetch in fetches:
        source = source_by_key[fetch.source_key]
        if source.key in failed_status_by_key:
            statuses.append(
                SourceStatus(
                    key=source.key,
                    name=source.name,
                    status=failed_status_by_key[source.key],
                    event_count=0,
                    via_browser=fetch.via_browser,
                    error=error_by_key[source.key],
                )
            )
        elif source.key in cached_events_by_key:
            in_window = [
                e
                for e in cached_events_by_key[source.key]
                if _within_window(e, today, window_end)
            ]
            all_events.extend(in_window)
            statuses.append(
                SourceStatus(
                    key=source.key,
                    name=source.name,
                    status=FetchStatus.OK,
                    event_count=len(in_window),
                    from_cache=True,
                    via_browser=fetch.via_browser,
                )
            )
        else:
            outcome = outcome_by_key[source.key]
            if outcome.error is not None:
                statuses.append(
                    SourceStatus(
                        key=source.key,
                        name=source.name,
                        status=FetchStatus.ERROR,
                        event_count=0,
                        via_browser=fetch.via_browser,
                        error=outcome.error,
                    )
                )
                continue
            total_cost += outcome.cost_usd
            parsed = list(outcome.events)
            working_cache = store_cached_events(
                working_cache, fetch.url, fetch_by_key[source.key].content_hash, parsed
            )
            in_window = [e for e in parsed if _within_window(e, today, window_end)]
            all_events.extend(in_window)
            statuses.append(
                SourceStatus(
                    key=source.key,
                    name=source.name,
                    status=FetchStatus.OK,
                    event_count=len(in_window),
                    via_browser=fetch.via_browser,
                )
            )
    return all_events, statuses, working_cache, total_cost


# --- Stage: locate ---------------------------------------------------------


def _geocode_query_for(event: Event) -> str | None:
    if event.address:
        return event.address
    if event.venue and event.city:
        return f"{event.venue}, {event.city}"
    return event.venue or event.city


def do_locate(
    config: RadarRunConfig, events: list[Event], geocache: dict
) -> tuple[list[Event], dict]:
    """Geocode venues (cached) and compute drive time from home for each event."""
    working_cache = geocache
    located: list[Event] = []
    for event in events:
        # Recover a clean city name (Funcheap addresses can carry a street
        # number) and derive the coarse area from it, for display + preferences.
        clean_city = (
            canonical_city(event.city, event.address, event.venue) or event.city
        )
        area = derive_area(clean_city)
        query = _geocode_query_for(event)
        coords: tuple[float, float] | None = None
        if query:
            cached = lookup_geocode(working_cache, query)
            if cached == "miss":
                coords = geocode(query)
                working_cache = store_geocode(working_cache, query, coords)
            else:
                coords = cached  # coords tuple or None (known-unresolvable)
        if coords is None:
            located.append(
                event.model_copy(
                    update={
                        "city": clean_city,
                        "location_status": LocationStatus.TBD,
                        "area": area,
                    }
                )
            )
            continue
        drive = compute_drive_minutes(config.home_coords, coords)
        located.append(
            event.model_copy(
                update={
                    "city": clean_city,
                    "venue_latitude": coords[0],
                    "venue_longitude": coords[1],
                    "drive_minutes": drive,
                    "location_status": LocationStatus.RESOLVED,
                    "area": area,
                }
            )
        )
    return located, working_cache


# --- Stage: assemble -------------------------------------------------------


def _load_preference_profile(path: Path) -> PreferenceProfile | None:
    if not path.exists():
        return None
    try:
        return PreferenceProfile.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as error:
        print(
            f"[assemble] ignoring unreadable preference profile: {error}",
            file=sys.stderr,
        )
        return None


def do_assemble(
    config: RadarRunConfig,
    events: list[Event],
    statuses: list[SourceStatus],
    sources: RadarSources,
    profile: PreferenceProfile | None,
    fetched_at: str,
):
    window_end = compute_window_end(config.today_date, config.weeks_ahead)
    pulled = tuple(
        status.name for status in statuses if status.status == FetchStatus.OK
    )
    return build_radar_output(
        events=events,
        today=config.today_date,
        window_end=window_end,
        weeks_ahead=config.weeks_ahead,
        radius_minutes=config.drive_radius_minutes,
        home_description=config.home_description,
        home_address=config.home_address,
        home_latitude=config.home_latitude,
        home_longitude=config.home_longitude,
        age_min=config.age_min,
        age_max=config.age_max,
        sources_pulled=pulled,
        source_status=tuple(statuses),
        panel=sources.panel,
        fetched_at=fetched_at,
        profile=profile,
    )


def _prune_old_runs(retention: int) -> None:
    if not RUNS_DIR.exists():
        return
    run_dirs = sorted(
        (
            path
            for path in RUNS_DIR.iterdir()
            if path.is_dir() and path.name != "current"
        ),
        key=lambda path: path.name,
    )
    for stale in run_dirs[:-retention] if retention > 0 else run_dirs:
        shutil.rmtree(stale, ignore_errors=True)


# --- verify-source (adoption helper) ---------------------------------------

# URL shapes that are almost always a feed rather than an HTML page. When the URL
# looks like a feed we read it over plain HTTP and skip the JS-shell/browser
# fallback (which would wrongly "render" an XML document).
_FEED_URL_HINTS: tuple[str, ...] = ("/feed", "/rss", ".rss", ".xml", "/atom", "format=rss")
# How many example events to show in a verdict.
_VERIFY_EXAMPLE_COUNT = 5


class VerifyExample(BaseModel):
    """One example event surfaced in a verify-source verdict."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(description="Event title")
    date: str | None = Field(default=None, description="Event start date, YYYY-MM-DD")


class VerifySourceVerdict(BaseModel):
    """The result of verifying one candidate source URL.

    ``ok`` is the go/no-go signal (drives the process exit code): true when the
    page yields at least one dated, kid-appropriate event inside the window.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    url: str = Field(description="The candidate URL that was checked")
    city: str | None = Field(default=None, description="Adopter's city, if provided")
    ok: bool = Field(description="Whether the source yields usable dated events")
    fetched: bool = Field(description="Whether the URL could be fetched at all")
    event_count: int = Field(description="Dated, kid-appropriate events in the window")
    examples: tuple[VerifyExample, ...] = Field(
        default=(), description="A few example events found"
    )
    parser: ParserKind = Field(description="The parser used for extraction")
    recommended_type: str = Field(
        description="What to put in sources.toml: rss / html / html+browser / none"
    )
    needs_browser: bool = Field(
        description="Whether the stealth browser was needed to get usable content"
    )
    error: str | None = Field(default=None, description="Fetch error detail, if any")


def _url_looks_like_feed(url: str) -> bool:
    lowered = url.lower()
    return any(hint in lowered for hint in _FEED_URL_HINTS)


def _looks_like_feed(content: str) -> bool:
    """True if the fetched payload is an RSS/Atom feed rather than an HTML page."""
    head = content[:4000].lower()
    if "<rss" in head or "<feed" in head:
        return True
    return "<channel" in head and "<item" in head


def _fetch_candidate(url: str, *, needs_browser: bool) -> tuple[str | None, bool]:
    """Fetch a candidate URL, returning (content, via_browser); (None, _) on failure.

    Reuses the pipeline's own fetch path (browser fallback for bot-blocked / JS
    pages). An explicit feed URL is read over plain HTTP first, so an XML feed is
    never mistaken for a JS shell and pushed through the browser.
    """
    if not needs_browser and _url_looks_like_feed(url):
        text = try_http_get(url)
        if text and _looks_like_feed(text):
            return text, False
    source = SourceConfig(
        key="candidate",
        name="Candidate source",
        url=url,
        parser=ParserKind.AI_HTML,
        needs_browser=needs_browser,
    )
    fetch = fetch_source(source)
    if fetch.status == FetchStatus.OK and fetch.content:
        return fetch.content, fetch.via_browser
    return None, fetch.via_browser


def _recommended_type(parser: ParserKind, via_browser: bool) -> str:
    if parser == ParserKind.AI_RSS:
        return "rss"
    return "html+browser" if via_browser else "html"


def build_verify_verdict(
    events: list[Event],
    *,
    url: str,
    city: str | None,
    parser: ParserKind,
    via_browser: bool,
    today: date,
    window_end: date,
) -> VerifySourceVerdict:
    """Assemble a verdict from already-extracted events (pure; no network/model).

    Keeps only dated events inside the window that clear the deterministic
    kid-appropriateness guardrail, so the count reflects what the source would
    actually contribute to a run.
    """
    usable = [
        event
        for event in events
        if _within_window(event, today, window_end)
        and not is_clearly_not_kid_event(event.title)
    ]
    examples = tuple(
        VerifyExample(title=event.title, date=event.date)
        for event in usable[:_VERIFY_EXAMPLE_COUNT]
    )
    return VerifySourceVerdict(
        url=url,
        city=city,
        ok=bool(usable),
        fetched=True,
        event_count=len(usable),
        examples=examples,
        parser=parser,
        recommended_type=_recommended_type(parser, via_browser),
        needs_browser=via_browser,
    )


def verify_source(
    url: str,
    *,
    city: str | None,
    today: date,
    weeks_ahead: int,
    age_min: int,
    age_max: int,
    model: str,
    needs_browser: bool,
) -> VerifySourceVerdict:
    """Fetch + extract one candidate URL and return a go/no-go verdict."""
    window_end = compute_window_end(today, weeks_ahead)
    content, via_browser = _fetch_candidate(url, needs_browser=needs_browser)
    if content is None:
        return VerifySourceVerdict(
            url=url,
            city=city,
            ok=False,
            fetched=False,
            event_count=0,
            parser=ParserKind.AI_HTML,
            recommended_type="none",
            needs_browser=via_browser or needs_browser,
            error="could not fetch the URL (blocked or unreachable)",
        )
    parser = ParserKind.AI_RSS if _looks_like_feed(content) else ParserKind.AI_HTML
    source = SourceConfig(
        key="candidate",
        name="Candidate source",
        url=url,
        parser=parser,
        needs_browser=needs_browser or via_browser,
    )
    try:
        result = extract_events(
            content,
            source,
            today=today,
            window_end=window_end,
            age_min=age_min,
            age_max=age_max,
            model=model,
        )
    except KidEventsRadarError as error:
        return VerifySourceVerdict(
            url=url,
            city=city,
            ok=False,
            fetched=True,
            event_count=0,
            parser=parser,
            recommended_type="none",
            needs_browser=via_browser,
            error=f"extraction failed: {error}",
        )
    return build_verify_verdict(
        list(result.events),
        url=url,
        city=city,
        parser=parser,
        via_browser=via_browser,
        today=today,
        window_end=window_end,
    )


def _format_verdict(verdict: VerifySourceVerdict) -> str:
    """Render a verdict as a concise, human-readable block for the CLI."""
    lines: list[str] = []
    headline = "USABLE" if verdict.ok else "NOT USABLE"
    lines.append(f"{headline}: {verdict.url}")
    if verdict.city:
        lines.append(f"  area: {verdict.city}")
    if not verdict.fetched:
        lines.append(f"  could not fetch: {verdict.error}")
        return "\n".join(lines)
    if verdict.error:
        lines.append(f"  note: {verdict.error}")
    lines.append(
        f"  dated kid-appropriate events in window: {verdict.event_count}"
    )
    for example in verdict.examples:
        lines.append(f"    - {example.date or 'date?'}  {example.title}")
    lines.append(f"  needs stealth browser: {'yes' if verdict.needs_browser else 'no'}")
    lines.append(f"  recommended sources.toml type: {verdict.recommended_type}")
    if verdict.ok:
        lines.append("")
        lines.append("  ready-to-paste sources.toml entry:")
        lines.append("    [[source]]")
        lines.append('    key = "CHOOSE_A_KEY"')
        lines.append('    name = "Source display name"')
        lines.append(f'    url = "{verdict.url}"')
        lines.append(f'    parser = "{verdict.parser.value}"')
        if verdict.needs_browser:
            lines.append("    needs_browser = true")
    return "\n".join(lines)


def _cmd_verify_source(args: argparse.Namespace) -> None:
    today = parse_iso_date(args.today) if args.today else datetime.now(timezone.utc).date()
    if today is None:
        raise KidEventsRadarError(f"invalid --today value: {args.today}")
    verdict = verify_source(
        args.url,
        city=args.city or None,
        today=today,
        weeks_ahead=args.weeks_ahead,
        age_min=args.age_min,
        age_max=args.age_max,
        model=args.model,
        needs_browser=args.needs_browser,
    )
    print(_format_verdict(verdict))
    sys.exit(0 if verdict.ok else 1)


# --- run all ---------------------------------------------------------------


def do_run_all(
    config: RadarRunConfig, sources: RadarSources, out_path: Path, profile_path: Path
) -> Path:
    """Chain every stage in-process and write the stable snapshot."""
    fetched_at = _now_utc_iso()
    run_dir = RUNS_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    print("[run] fetching sources...", file=sys.stderr)
    fetches = do_fetch(sources)
    _save_raw_payloads(run_dir, sources, fetches)

    print("[run] extracting events...", file=sys.stderr)
    parse_cache = load_parse_cache(PARSE_CACHE_PATH)
    candidates, statuses, parse_cache, extract_cost = do_extract(
        config, sources, fetches, parse_cache
    )
    save_parse_cache(PARSE_CACHE_PATH, parse_cache)

    print(f"[run] locating {len(candidates)} candidate events...", file=sys.stderr)
    geocache = load_geocache(GEOCACHE_PATH)
    located, geocache = do_locate(config, candidates, geocache)
    save_geocache(GEOCACHE_PATH, geocache)

    print("[run] curating events...", file=sys.stderr)
    curation = curate_events(
        located, age_min=config.age_min, age_max=config.age_max, model=config.model
    )

    print("[run] assembling snapshot...", file=sys.stderr)
    profile = _load_preference_profile(profile_path)
    output = do_assemble(
        config, list(curation.events), statuses, sources, profile, fetched_at
    )

    payload = output.model_dump_json(indent=2)
    (run_dir / "output.json").write_text(payload, encoding="utf-8")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")
    _prune_old_runs(RUN_RETENTION_COUNT)

    total_cost = extract_cost + curation.cost_usd
    weekend_count = sum(len(group.events) for group in output.weekends)
    print(
        f"[run] done: {weekend_count} weekend + {len(output.thu_fri_evening)} evening events, "
        f"{curation.dropped_count} dropped, model cost ${total_cost:.4f}. Wrote {out_path}",
        file=sys.stderr,
    )
    return out_path


# --- CLI -------------------------------------------------------------------


def _resolve_config(args: argparse.Namespace) -> RadarRunConfig:
    if args.home_coords:
        latitude_text, _, longitude_text = args.home_coords.partition(",")
        latitude, longitude = float(latitude_text), float(longitude_text)
    else:
        latitude, longitude = DEFAULT_HOME_LATITUDE, DEFAULT_HOME_LONGITUDE
    today = args.today or datetime.now(timezone.utc).date().isoformat()
    return RadarRunConfig(
        home_address=args.home_address,
        home_description=args.home_description,
        home_latitude=latitude,
        home_longitude=longitude,
        age_min=args.age_min,
        age_max=args.age_max,
        drive_radius_minutes=args.drive_radius_min,
        weeks_ahead=args.weeks_ahead,
        model=args.model,
        today=today,
    )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home-address", default=DEFAULT_HOME_ADDRESS)
    parser.add_argument("--home-description", default=DEFAULT_HOME_DESCRIPTION)
    parser.add_argument("--home-coords", default="", help="lat,lon; overrides defaults")
    parser.add_argument("--age-min", type=int, default=DEFAULT_AGE_MIN)
    parser.add_argument("--age-max", type=int, default=DEFAULT_AGE_MAX)
    parser.add_argument(
        "--drive-radius-min", type=int, default=DEFAULT_DRIVE_RADIUS_MINUTES
    )
    parser.add_argument("--weeks-ahead", type=int, default=DEFAULT_WEEKS_AHEAD)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--today", default="", help="override today's date (YYYY-MM-DD)"
    )
    parser.add_argument("--sources-config", default=str(DEFAULT_SOURCES_CONFIG_PATH))
    parser.add_argument("--run-dir", default=str(RUNS_DIR / "current"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="kid-events-radar pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("fetch", "extract", "locate", "curate"):
        stage_parser = subparsers.add_parser(name)
        _add_common_args(stage_parser)
    assemble_parser = subparsers.add_parser("assemble")
    _add_common_args(assemble_parser)
    assemble_parser.add_argument("--out", default=str(LATEST_SNAPSHOT_PATH))
    assemble_parser.add_argument(
        "--preference-profile", default=str(PREFERENCE_PROFILE_PATH)
    )
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("run_target", choices=["all"])
    _add_common_args(run_parser)
    run_parser.add_argument("--out", default=str(LATEST_SNAPSHOT_PATH))
    run_parser.add_argument(
        "--preference-profile", default=str(PREFERENCE_PROFILE_PATH)
    )
    verify_parser = subparsers.add_parser(
        "verify-source",
        help="check whether one candidate URL yields dated kid events",
    )
    verify_parser.add_argument("--url", required=True, help="candidate source URL")
    verify_parser.add_argument(
        "--city", default="", help='adopter city for context, e.g. "Austin, TX"'
    )
    verify_parser.add_argument("--today", default="", help="override today (YYYY-MM-DD)")
    verify_parser.add_argument("--age-min", type=int, default=DEFAULT_AGE_MIN)
    verify_parser.add_argument("--age-max", type=int, default=DEFAULT_AGE_MAX)
    verify_parser.add_argument("--weeks-ahead", type=int, default=DEFAULT_WEEKS_AHEAD)
    verify_parser.add_argument("--model", default=DEFAULT_MODEL)
    verify_parser.add_argument(
        "--needs-browser",
        action="store_true",
        help="force fetching via the stealth browser (skip plain HTTP)",
    )
    return parser


def _cmd_fetch(config: RadarRunConfig, sources: RadarSources, run_dir: Path) -> None:
    fetches = do_fetch(sources)
    _save_raw_payloads(run_dir, sources, fetches)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fetch_manifest.json").write_text(
        json.dumps([fetch.model_dump(mode="json") for fetch in fetches], indent=2),
        encoding="utf-8",
    )
    ok = sum(1 for fetch in fetches if fetch.status == FetchStatus.OK)
    print(
        f"fetched {ok}/{len(fetches)} sources; manifest in {run_dir}", file=sys.stderr
    )


def _load_fetch_manifest(run_dir: Path) -> list[RawFetch]:
    raw = json.loads((run_dir / "fetch_manifest.json").read_text(encoding="utf-8"))
    return [RawFetch.model_validate(item) for item in raw]


def _cmd_extract(config: RadarRunConfig, sources: RadarSources, run_dir: Path) -> None:
    fetches = _load_fetch_manifest(run_dir)
    parse_cache = load_parse_cache(PARSE_CACHE_PATH)
    candidates, statuses, parse_cache, cost = do_extract(
        config, sources, fetches, parse_cache
    )
    save_parse_cache(PARSE_CACHE_PATH, parse_cache)
    _write_events(run_dir / "candidates.json", candidates)
    _write_statuses(run_dir / "source_status.json", statuses)
    print(
        f"extracted {len(candidates)} candidates; model cost ${cost:.4f}",
        file=sys.stderr,
    )


def _cmd_locate(config: RadarRunConfig, run_dir: Path) -> None:
    candidates = _read_events(run_dir / "candidates.json")
    geocache = load_geocache(GEOCACHE_PATH)
    located, geocache = do_locate(config, candidates, geocache)
    save_geocache(GEOCACHE_PATH, geocache)
    _write_events(run_dir / "located.json", located)
    resolved = sum(
        1 for event in located if event.location_status == LocationStatus.RESOLVED
    )
    print(f"located {resolved}/{len(located)} events", file=sys.stderr)


def _cmd_curate(config: RadarRunConfig, run_dir: Path) -> None:
    located = _read_events(run_dir / "located.json")
    curation = curate_events(
        located, age_min=config.age_min, age_max=config.age_max, model=config.model
    )
    _write_events(run_dir / "curated.json", list(curation.events))
    print(
        f"curated: kept {len(curation.events)}, dropped {curation.dropped_count}, "
        f"cost ${curation.cost_usd:.4f}",
        file=sys.stderr,
    )


def _cmd_assemble(
    config: RadarRunConfig,
    sources: RadarSources,
    run_dir: Path,
    args: argparse.Namespace,
) -> None:
    curated = _read_events(run_dir / "curated.json")
    statuses = _read_statuses(run_dir / "source_status.json")
    profile = _load_preference_profile(Path(args.preference_profile))
    output = do_assemble(config, curated, statuses, sources, profile, _now_utc_iso())
    payload = output.model_dump_json(indent=2)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(payload, encoding="utf-8")
    (run_dir / "output.json").write_text(payload, encoding="utf-8")
    print(f"assembled snapshot -> {out_path}", file=sys.stderr)


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # verify-source is a standalone adoption helper: it needs no home config,
    # sources file, or run directory, so handle it before resolving those.
    if args.command == "verify-source":
        _cmd_verify_source(args)
        return

    config = _resolve_config(args)
    sources = load_sources_config(Path(args.sources_config))
    run_dir = Path(args.run_dir)

    if args.command == "fetch":
        _cmd_fetch(config, sources, run_dir)
    elif args.command == "extract":
        _cmd_extract(config, sources, run_dir)
    elif args.command == "locate":
        _cmd_locate(config, run_dir)
    elif args.command == "curate":
        _cmd_curate(config, run_dir)
    elif args.command == "assemble":
        _cmd_assemble(config, sources, run_dir, args)
    elif args.command == "run":
        do_run_all(config, sources, Path(args.out), Path(args.preference_profile))
    else:
        raise KidEventsRadarError(f"unknown command: {args.command}")


if __name__ == "__main__":
    main()
