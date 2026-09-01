"""Unit and fixture tests for the kid-events-radar pipeline.

These exercise every deterministic stage against saved fixtures and synthetic
data with no network or model calls: the parsers (Funcheap JSON-LD, permalinks,
RSS, HTML->markdown), the model-output coercion, the caches, the date helpers,
and the assemble/rank/bucket logic including the preference-profile de-emphasis.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import assembling
import caching
import curating
import extraction
import fetching
import parsing
import radar_config
import radar_dates
import run
from radar_types import (
    Event,
    LinkQuality,
    LocationStatus,
    ParserKind,
    PreferenceProfile,
    SourceConfig,
    TimeConfidence,
)

_FIXTURES_DIR = _SCRIPTS_DIR.parent / "tests" / "fixtures"


def _fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def _funcheap_source() -> SourceConfig:
    return SourceConfig(
        key="funcheap",
        name="SF Funcheap",
        url="https://sf.funcheap.com/category/event/event-types/kids-families/",
        parser=ParserKind.FUNCHEAP,
    )


def _make_event(**overrides: object) -> Event:
    base = dict(
        source_key="src",
        source_name="Src",
        title="An Event",
        date="2026-08-22",
        source_url="https://example.org/e",
        link_quality=LinkQuality.EVENT,
        raw_excerpt="excerpt",
    )
    base.update(overrides)
    return Event.model_validate(base)


# --- parsing: Funcheap JSON-LD event page ----------------------------------


def test_parse_funcheap_event_reads_jsonld_shape() -> None:
    event = parsing.parse_funcheap_event(
        _fixture("funcheap_event.html"),
        "https://sf.funcheap.com/rockridge-rocknstroll/",
        _funcheap_source(),
    )
    assert event is not None
    # HTML entities in the JSON-LD name are unescaped.
    assert (
        event.title
        == "Rockridge Free “Rock-N-Stroll” w/ 20+ Bands, 50+ Local Artists (2026)"
    )
    assert event.date == "2026-08-22"
    assert event.end_date == "2026-08-22"
    assert event.day == "Saturday"
    assert event.start_time == "11:00 AM"
    assert event.venue == "Rockridge"
    assert event.city == "Oakland"
    assert event.address == "5660 College Ave.,Oakland, CA"
    assert event.price_text == "FREE"
    assert event.link_quality == LinkQuality.EVENT
    assert event.source_url == "https://sf.funcheap.com/rockridge-rocknstroll/"


def test_parse_funcheap_event_without_jsonld_returns_none() -> None:
    assert (
        parsing.parse_funcheap_event(
            "<html><body>no ld</body></html>", "u", _funcheap_source()
        )
        is None
    )


def test_parse_funcheap_event_flags_self_contradicting_time_as_low_confidence() -> None:
    # The canonical bad-data case: the page's structured time (7 p.m.) conflicts
    # with its prose (12-5 p.m.). Capture BOTH; do not auto-prefer either.
    event = parsing.parse_funcheap_event(
        _fixture("funcheap_duboce_conflict.html"),
        "https://sf.funcheap.com/2026-duboce-park-parkjam-free-festival-sf/",
        _funcheap_source(),
    )
    assert event is not None
    assert event.time_confidence == TimeConfidence.LOW
    # Both candidate times are preserved for the UI's "verify time" hint.
    assert event.time_candidates.get("structured") == "7:00 PM"
    assert "12 to 5" in event.time_candidates.get("prose", "")
    # The event is kept, not dropped, despite the conflict.
    assert event.date == "2026-08-22"


def test_parse_funcheap_event_marks_high_confidence_when_no_conflict() -> None:
    event = parsing.parse_funcheap_event(
        _fixture("funcheap_event.html"),
        "https://sf.funcheap.com/rockridge-rocknstroll/",
        _funcheap_source(),
    )
    assert event is not None
    # A single structured time with no conflicting prose time -> high confidence.
    assert event.time_confidence == TimeConfidence.HIGH
    assert event.time_candidates == {}


# --- parsing: Funcheap listing permalinks ----------------------------------


def test_parse_funcheap_permalinks_extracts_events_and_excludes_index_pages() -> None:
    permalinks = parsing.parse_funcheap_permalinks(
        _fixture("funcheap_listing.html"),
        "https://sf.funcheap.com/category/event/event-types/kids-families/",
    )
    assert permalinks == [
        "https://sf.funcheap.com/rockridge-rocknstroll/",
        "https://sf.funcheap.com/2026-duboce-park-parkjam-free-festival-sf/",
        # relative href resolved against the listing URL
        "https://sf.funcheap.com/summer-love-pier-39/",
    ]


# --- parsing: HTML -> markdown preserves links -----------------------------


def test_html_to_markdown_preserves_anchor_links_and_resolves_relative() -> None:
    html = '<div><p>See <a href="/duboce">Duboce ParkJam</a> Saturday.</p></div>'
    markdown = parsing.html_to_markdown(html, "https://example.org/events")
    assert "[Duboce ParkJam](https://example.org/duboce)" in markdown


def test_html_to_markdown_survives_non_self_closed_void_tags() -> None:
    # A non-self-closed <meta>/<link> (HTML5 void elements) must not swallow the
    # document body -- the regression that produced empty markdown on real pages.
    html = (
        "<html><head><meta charset='utf-8'><link rel='stylesheet' href='x.css'>"
        "<title>Ignored</title></head>"
        "<body><p>Events this August weekend:</p>"
        "<ul><li>Saturday festival</li></ul></body></html>"
    )
    markdown = parsing.html_to_markdown(html, "https://example.org")
    assert "Events this August weekend" in markdown
    assert "Saturday festival" in markdown
    assert "Ignored" not in markdown


# --- parsing: RSS ----------------------------------------------------------


def test_parse_rss_items_reads_titles_links_and_content() -> None:
    items = parsing.parse_rss_items(_fixture("sample_feed.xml"))
    assert len(items) == 2
    assert items[0].title == "This Weekend in SF: 5 Free Family Picks (Aug 22-23)"
    assert items[0].link == "https://kidssf.substack.com/p/this-weekend-aug-22"
    assert "Duboce Park ParkJam" in items[0].content_html
    # Second item falls back to <description> when there is no content:encoded.
    assert "Japanese Tea Garden" in items[1].content_html


# --- extraction: coercion of model output (no LLM) -------------------------


def test_strip_to_json_array_handles_code_fences() -> None:
    fenced = '```json\n[{"a": 1}]\n```'
    assert extraction._strip_to_json_array(fenced) == '[{"a": 1}]'


def test_coerce_event_uses_event_url_and_drops_out_of_window() -> None:
    source = SourceConfig(
        key="mp",
        name="Mommy Poppins SF",
        url="https://mp.example/land",
        parser=ParserKind.AI_HTML,
    )
    today = date(2026, 8, 21)
    window_end = date(2026, 10, 16)
    in_window = extraction._coerce_event(
        {
            "title": "Cookie Fest",
            "date": "2026-08-22",
            "source_url": "https://mp.example/cookie",
            "raw_excerpt": "x",
        },
        source,
        today=today,
        window_end=window_end,
    )
    assert in_window is not None
    assert in_window.source_url == "https://mp.example/cookie"
    assert in_window.link_quality == LinkQuality.EVENT
    # No usable URL -> falls back to the landing page and marks link_quality.
    landing = extraction._coerce_event(
        {
            "title": "No URL Fest",
            "date": "2026-08-23",
            "source_url": None,
            "raw_excerpt": "x",
        },
        source,
        today=today,
        window_end=window_end,
    )
    assert landing is not None
    assert landing.source_url == "https://mp.example/land"
    assert landing.link_quality == LinkQuality.LANDING
    # Past-dated events are dropped at coercion.
    assert (
        extraction._coerce_event(
            {
                "title": "Old",
                "date": "2026-08-01",
                "source_url": None,
                "raw_excerpt": "x",
            },
            source,
            today=today,
            window_end=window_end,
        )
        is None
    )


# --- date helpers ----------------------------------------------------------


def test_weekend_helpers() -> None:
    saturday = date(2026, 8, 22)
    sunday = date(2026, 8, 23)
    assert radar_dates.saturday_of_weekend(saturday) == saturday
    assert radar_dates.saturday_of_weekend(sunday) == saturday
    assert radar_dates.weekend_label(saturday) == "Aug 22-23"
    assert radar_dates.weekday_label(saturday) == "Saturday"
    assert radar_dates.is_weekend(saturday) and not radar_dates.is_weekend(
        date(2026, 8, 20)
    )
    assert radar_dates.is_thursday_or_friday(date(2026, 8, 20))


def test_weekend_label_crosses_month_boundary() -> None:
    assert radar_dates.weekend_label(date(2026, 8, 29)) == "Aug 29-30"
    assert radar_dates.weekend_label(date(2026, 10, 31)) == "Oct 31-Nov 1"


def test_human_time_from_iso() -> None:
    assert radar_dates.human_time_from_iso("2026-08-22T11:00:00-07:00") == "11:00 AM"
    assert radar_dates.human_time_from_iso("2026-08-22T17:30:00-07:00") == "5:30 PM"
    assert radar_dates.human_time_from_iso("2026-08-22") is None


# --- caching ---------------------------------------------------------------


def test_parse_cache_hit_only_on_matching_hash() -> None:
    events = [_make_event(title="Cached Event")]
    cache = caching.store_cached_events(
        {"version": 1, "entries": {}}, "u", "hash-1", events
    )
    hit = caching.lookup_cached_events(cache, "u", "hash-1")
    assert hit is not None and hit[0].title == "Cached Event"
    assert caching.lookup_cached_events(cache, "u", "hash-2") is None
    assert caching.lookup_cached_events(cache, "other", "hash-1") is None


def test_geocache_distinguishes_null_from_miss() -> None:
    cache = caching.store_geocode(
        {"version": 1, "entries": {}}, "SF Zoo", (37.73, -122.5)
    )
    assert caching.lookup_geocode(cache, "sf zoo") == (37.73, -122.5)
    cache_null = caching.store_geocode(cache, "Nowhere", None)
    assert caching.lookup_geocode(cache_null, "Nowhere") is None
    assert caching.lookup_geocode(cache_null, "Never Seen") == "miss"


# --- assembling: dedupe / bucket / radius ----------------------------------


def test_dedupe_prefers_event_link_and_resolved_location() -> None:
    weak = _make_event(title="Duboce ParkJam", link_quality=LinkQuality.LANDING)
    strong = _make_event(
        title="Duboce ParkJam!",
        link_quality=LinkQuality.EVENT,
        location_status=LocationStatus.RESOLVED,
    )
    deduped = assembling.dedupe_events([weak, strong])
    assert len(deduped) == 1
    assert deduped[0].link_quality == LinkQuality.EVENT


# --- assembling: fuzzy de-dup across sources (heal regression) --------------

# The four real duplicate pairs that appeared twice under slightly different
# titles from different sources. Each pair MUST collapse to one event.
_DUPLICATE_PAIRS: tuple[tuple[dict[str, object], dict[str, object]], ...] = (
    (
        {
            "title": "8th Annual Crab Cove Fish Festival (Alameda)",
            "venue": "Crab Cove Visitor Center",
            "city": "Alameda",
        },
        {
            "title": "Crab Cove Fish Festival",
            "venue": "Doug Siden Visitor Center at Crab Cove, Crown Memorial State Beach",
            "city": "Alameda",
        },
    ),
    (
        {
            "title": "44th Annual Point Reyes Sand Sculpture Contest (Drakes Beach)",
            "venue": "Drakes Beach",
            "city": "Point Reyes Station",
        },
        {
            "title": "44th Annual Sand Sculpture Contest",
            "venue": "Drakes Beach",
            "city": "Point Reyes",
        },
    ),
    (
        {
            "title": "Richmond Art Center 90th Anniversary Celebration",
            "venue": "Richmond Art Center",
            "city": "Richmond",
        },
        {
            "title": "Richmond Art Center’s 90th Birthday Party",
            "venue": "Richmond Art Center",
            "city": "Richmond",
        },
    ),
    (
        {
            "title": "Community Gardens 20th Anniversary",
            "venue": "Participating community gardens citywide",
            "city": "San Francisco",
        },
        {
            "title": "SF Rec & Parks Community Gardens 20th Anniversary: Tours & Potluck (2026)",
            "venue": None,
            "city": None,
        },
    ),
)


def test_dedupe_collapses_each_known_duplicate_pair() -> None:
    for left, right in _DUPLICATE_PAIRS:
        pair = [
            _make_event(date="2026-08-29", **left),
            _make_event(date="2026-08-29", **right),
        ]
        deduped = assembling.dedupe_events(pair)
        assert len(deduped) == 1, f"did not merge: {left['title']} == {right['title']}"


def test_dedupe_collapses_all_pairs_together_and_keeps_distinct_events() -> None:
    events = []
    for left, right in _DUPLICATE_PAIRS:
        events.append(_make_event(date="2026-08-29", **left))
        events.append(_make_event(date="2026-08-29", **right))
    # A genuinely different event on the same date must survive alongside them.
    events.append(
        _make_event(date="2026-08-29", title="Obon Festival", venue="Buddhist Church")
    )
    deduped = assembling.dedupe_events(events)
    assert len(deduped) == len(_DUPLICATE_PAIRS) + 1


def test_dedupe_normalizes_ordinals_and_parenthetical_suffixes() -> None:
    assert (
        assembling._normalized_title("8th Annual Crab Cove Fish Festival (Alameda)")
        == "crab cove fish festival"
    )
    assert (
        assembling._normalized_title("Richmond Art Center’s 90th Birthday Party")
        == "richmond art center 90th birthday party"
    )


def test_dedupe_does_not_merge_similar_titles_at_different_venues() -> None:
    # Similar wording, same date and city, but DIFFERENT venues -> two events.
    events = [
        _make_event(
            date="2026-08-29",
            title="India Basin Family Day",
            venue="India Basin",
            city="San Francisco",
        ),
        _make_event(
            date="2026-08-29",
            title="Bean Sprouts Family Days",
            venue="Bean Sprouts Cafe",
            city="San Francisco",
        ),
    ]
    assert len(assembling.dedupe_events(events)) == 2


def test_dedupe_does_not_merge_token_subset_titles_at_different_venues() -> None:
    # Same date and city, and one title's tokens are a subset of the other's, but
    # the venues contradict -> two genuinely different events, not a duplicate.
    events = [
        _make_event(
            date="2026-08-29",
            title="Summer Festival",
            venue="Marina Green",
            city="San Francisco",
        ),
        _make_event(
            date="2026-08-29",
            title="Summer Festival at Dolores Park with Fireworks",
            venue="Dolores Park",
            city="San Francisco",
        ),
    ]
    assert len(assembling.dedupe_events(events)) == 2


def test_dedupe_does_not_merge_same_title_on_different_dates() -> None:
    # A recurring event on different dates is not a duplicate.
    events = [
        _make_event(date="2026-09-03", title="Bean Sprouts Family Days", venue="Garden"),
        _make_event(date="2026-09-10", title="Bean Sprouts Family Days", venue="Garden"),
    ]
    assert len(assembling.dedupe_events(events)) == 2


def test_dedupe_is_order_independent_and_keeps_best_record() -> None:
    landing = _make_event(
        date="2026-08-29",
        title="8th Annual Crab Cove Fish Festival (Alameda)",
        venue="Crab Cove Visitor Center",
        city="Alameda",
        link_quality=LinkQuality.LANDING,
    )
    event_link = _make_event(
        date="2026-08-29",
        title="Crab Cove Fish Festival",
        venue="Doug Siden Visitor Center at Crab Cove",
        city="Alameda",
        link_quality=LinkQuality.EVENT,
        location_status=LocationStatus.RESOLVED,
    )
    for ordering in ([landing, event_link], [event_link, landing]):
        deduped = assembling.dedupe_events(ordering)
        assert len(deduped) == 1
        assert deduped[0].link_quality == LinkQuality.EVENT


# --- curating: deterministic drop guardrail (heal regression) --------------

# The six real events that were wrongly kept and must now be dropped.
_MUST_DROP_TITLES: tuple[str, ...] = (
    "“Manny’s Neighborhood Trash Cleanup” w/ $1 Beer & Free Yoga Classes, Free Fries (SF)",
    "Portola Neighborhood Garage Sale 2026",
    "Umoja Health Access Point (HAP) Community Health Fair",
    "“The Giant Crane” Free Shipyard History Talk by Stacey Carter at the Shipyard Gallery (SF)",
    "Golden Gate Park “The Whale’s Tail” Beer/Wine Garden + Live Music (Summer 2026)",
    "Free “Castro Carnival” Block Party: LGBTQ+ Art & Drag Shows (SF)",
)

# Family-friendly community events that must be KEPT (not tripped by the guardrail).
_MUST_KEEP_TITLES: tuple[str, ...] = (
    "Community Gardens 20th Anniversary",
    "SF Rec & Parks Community Gardens 20th Anniversary: Tours & Potluck (2026)",
    "44th Annual Sand Sculpture Contest",
    "Richmond Art Center 90th Anniversary Celebration",
    "8th Annual Crab Cove Fish Festival (Alameda)",
    "BonPOP Obon Festival",
    "Cinema on the Square: Pokémon Detective Pikachu",
    "Puppet Fair Weekend at Children's Fairyland",
    "Bubble Bonanza",
    "India Basin Family Day",
    "Cal Sailing Open House",
    # Tricky keeps: a family word rescues the overridable categories, and the
    # always-drop tier is narrow enough not to catch these.
    "Drag Queen Story Hour at the Library",
    "Root Beer Float Social for Families",
    "Family Flea Market with Kids Craft Corner",
)


def test_guardrail_drops_every_known_adult_or_transactional_event() -> None:
    for title in _MUST_DROP_TITLES:
        assert curating.is_clearly_not_kid_event(title) is True, f"should drop: {title}"


def test_guardrail_keeps_family_friendly_community_events() -> None:
    for title in _MUST_KEEP_TITLES:
        assert curating.is_clearly_not_kid_event(title) is False, f"should keep: {title}"


def test_curate_guardrail_drops_before_the_model_without_a_model_call() -> None:
    # Feeding only guardrail-droppable events curates to empty with zero cost --
    # proving the deterministic layer runs before (and without) the model.
    events = [_make_event(title=title) for title in _MUST_DROP_TITLES]
    result = curating.curate_events(events, age_min=4, age_max=7, model="unused-model")
    assert result.events == ()
    assert result.dropped_count == len(_MUST_DROP_TITLES)
    assert result.cost_usd == 0.0


def test_bucket_for_event_classifies_weekend_evening_and_drops_midweek() -> None:
    today = date(2026, 8, 21)
    window_end = date(2026, 10, 16)
    weekend = assembling.bucket_for_event(
        _make_event(date="2026-08-23"), today, window_end
    )
    assert weekend == ("weekend", date(2026, 8, 22))
    evening = assembling.bucket_for_event(
        _make_event(date="2026-08-27"), today, window_end
    )
    assert evening == ("evening", None)
    # A Tuesday event fits no bucket.
    assert (
        assembling.bucket_for_event(_make_event(date="2026-08-25"), today, window_end)
        is None
    )
    # A multi-day event covering a weekend is bucketed as weekend.
    multiday = assembling.bucket_for_event(
        _make_event(date="2026-08-19", end_date="2026-08-23"), today, window_end
    )
    assert multiday == ("weekend", date(2026, 8, 22))


def test_is_within_radius_keeps_tbd_events() -> None:
    assert assembling.is_within_radius(_make_event(drive_minutes=None), 60) is True
    assert assembling.is_within_radius(_make_event(drive_minutes=50), 60) is True
    assert assembling.is_within_radius(_make_event(drive_minutes=75), 60) is False


def test_drive_bucket_boundaries() -> None:
    assert assembling.drive_bucket(10) == "0-15"
    assert assembling.drive_bucket(45) == "30-45"
    assert assembling.drive_bucket(60) == "45-60"
    assert assembling.drive_bucket(75) == "60+"
    assert assembling.drive_bucket(None) == "unknown"


# --- assembling: preference-profile de-emphasis ranking --------------------


def test_ranking_is_drive_time_ascending_without_profile() -> None:
    events = [
        _make_event(title="Far", drive_minutes=50),
        _make_event(title="Near", drive_minutes=10),
    ]
    ranked = assembling.rank_events(events, None)
    assert [event.title for event in ranked] == ["Near", "Far"]


def test_preference_profile_sinks_disliked_events_without_removing_them() -> None:
    near_disliked = _make_event(
        title="Near but disliked",
        drive_minutes=10,
        source_name="Mommy Poppins SF",
        city="San Jose",
    )
    far_liked = _make_event(
        title="Far but liked",
        drive_minutes=45,
        source_name="SF Funcheap",
        city="San Francisco",
    )
    profile = PreferenceProfile(
        source={"Mommy Poppins SF": 2.0}, city={"San Jose": 2.0}
    )
    ranked = assembling.rank_events([near_disliked, far_liked], profile)
    # Disliked near event picks up 4 weights * 20 = 80 penalty minutes (10+80=90),
    # sinking it below the 45-minute liked event -- but it is still present.
    assert [event.title for event in ranked] == ["Far but liked", "Near but disliked"]
    assert len(ranked) == 2
    sunk = next(event for event in ranked if event.title == "Near but disliked")
    assert sunk.de_emphasis_penalty_minutes == 80
    assert any("source" in reason for reason in sunk.de_emphasis_reasons)


def test_compute_de_emphasis_zero_when_nothing_matches() -> None:
    penalty, reasons = assembling.compute_de_emphasis(
        _make_event(source_name="SF Funcheap", city="San Francisco"),
        PreferenceProfile(city={"San Jose": 1.0}),
    )
    assert penalty == 0 and reasons == ()


# --- fetching: JS-shell detection ------------------------------------------


def test_needs_browser_render_detects_js_shells() -> None:
    # Almost no visible text -> needs rendering.
    assert (
        fetching.needs_browser_render("<html><body><div></div></body></html>") is True
    )
    # Plenty of text but no dates (a nav-only shell) -> needs rendering.
    nav_shell = (
        "<html><body>"
        + ("<a>Menu Login Directory Guide Events</a> " * 60)
        + "</body></html>"
    )
    assert fetching.needs_browser_render(nav_shell) is True
    # A real listing with several dates -> no rendering needed.
    listing = (
        "<html><body>"
        + " ".join(
            f"<p>Event on Aug {day}, live music and kids activities</p>"
            for day in range(21, 28)
        )
        + ("filler text " * 100)
        + "</body></html>"
    )
    assert fetching.needs_browser_render(listing) is False


def test_compute_content_hash_is_stable_and_distinct() -> None:
    assert fetching.compute_content_hash("abc") == fetching.compute_content_hash("abc")
    assert fetching.compute_content_hash("abc") != fetching.compute_content_hash("abd")


# --- config loading --------------------------------------------------------


def test_load_sources_config_parses_real_assets() -> None:
    sources = radar_config.load_sources_config(radar_config.DEFAULT_SOURCES_CONFIG_PATH)
    keys = {source.key for source in sources.sources}
    assert "funcheap" in keys
    funcheap = next(source for source in sources.sources if source.key == "funcheap")
    assert funcheap.parser == ParserKind.FUNCHEAP
    goldengatepark = next(
        source for source in sources.sources if source.key == "goldengatepark"
    )
    assert goldengatepark.needs_browser is True
    assert len(sources.panel) == 3


# --- verify-source (adoption helper) ---------------------------------------


def test_looks_like_feed_distinguishes_rss_from_html() -> None:
    assert run._looks_like_feed(_fixture("sample_feed.xml")) is True
    # An event HTML page is not a feed.
    assert run._looks_like_feed(_fixture("funcheap_event.html")) is False


def test_url_looks_like_feed_matches_feed_shaped_urls() -> None:
    assert run._url_looks_like_feed("https://kidssf.substack.com/feed") is True
    assert run._url_looks_like_feed("https://example.org/events.xml") is True
    assert run._url_looks_like_feed("https://example.org/events/") is False


def test_recommended_type_maps_parser_and_browser_to_sources_toml_type() -> None:
    assert run._recommended_type(ParserKind.AI_RSS, False) == "rss"
    assert run._recommended_type(ParserKind.AI_HTML, False) == "html"
    assert run._recommended_type(ParserKind.AI_HTML, True) == "html+browser"


def test_build_verify_verdict_passes_source_with_dated_kid_events() -> None:
    today = date(2026, 8, 21)
    window_end = date(2026, 10, 16)
    events = [
        _make_event(title="Bubble Bonanza", date="2026-08-22"),
        _make_event(title="Puppet Fair at Fairyland", date="2026-08-29"),
    ]
    verdict = run.build_verify_verdict(
        events,
        url="https://example.org/kids",
        city="Austin, TX",
        parser=ParserKind.AI_HTML,
        via_browser=False,
        today=today,
        window_end=window_end,
    )
    assert verdict.ok is True
    assert verdict.fetched is True
    assert verdict.event_count == 2
    assert verdict.recommended_type == "html"
    assert verdict.needs_browser is False
    assert {example.title for example in verdict.examples} == {
        "Bubble Bonanza",
        "Puppet Fair at Fairyland",
    }


def test_build_verify_verdict_rejects_empty_page() -> None:
    verdict = run.build_verify_verdict(
        [],
        url="https://example.org/not-events",
        city=None,
        parser=ParserKind.AI_HTML,
        via_browser=False,
        today=date(2026, 8, 21),
        window_end=date(2026, 10, 16),
    )
    assert verdict.ok is False
    assert verdict.event_count == 0
    assert verdict.examples == ()


def test_build_verify_verdict_excludes_out_of_window_and_adult_events() -> None:
    today = date(2026, 8, 21)
    window_end = date(2026, 10, 16)
    events = [
        _make_event(title="Family Story Time", date="2026-08-22"),  # kept
        _make_event(title="Past Fair", date="2026-08-01"),  # before today
        _make_event(title="Way Off Festival", date="2027-01-01"),  # after window
        _make_event(title="Downtown Pub Crawl", date="2026-08-29"),  # guardrail drop
    ]
    verdict = run.build_verify_verdict(
        events,
        url="https://example.org/mixed",
        city=None,
        parser=ParserKind.AI_RSS,
        via_browser=False,
        today=today,
        window_end=window_end,
    )
    assert verdict.ok is True
    assert verdict.event_count == 1
    assert verdict.recommended_type == "rss"
    assert [example.title for example in verdict.examples] == ["Family Story Time"]
