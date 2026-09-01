#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Deterministic parsers: Funcheap JSON-LD, RSS feeds, and HTML->markdown.

These are pure functions over already-fetched payloads (no network I/O), so
they are straightforward to unit-test against saved fixtures.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field
from radar_dates import human_time_from_iso, parse_iso_date, weekday_label
from radar_types import Event, LinkQuality, SourceConfig, TimeConfidence

# A time range in prose, e.g. "12 to 5 p.m.", "7-9pm", "10 a.m. to 4 p.m.".
_TIME_RANGE_RE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)?\s*(?:to|through|–|—|-)\s*"
    r"(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)",
    re.IGNORECASE,
)

# Tags whose contents are dropped. All have a matching close tag; void elements
# (meta, link, etc.) are deliberately excluded -- a non-self-closed void tag has
# no end tag, so skip-depth counting would never unwind and would swallow the
# rest of the document.
_SKIP_TAGS = frozenset({"script", "style", "noscript", "svg", "head", "template"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "table",
        "tr",
    }
)
_EVENT_LD_TYPES = frozenset(
    {
        "Event",
        "Festival",
        "SocialEvent",
        "MusicEvent",
        "VisualArtsEvent",
        "ChildrensEvent",
        "TheaterEvent",
        "ScreeningEvent",
    }
)


class RssItem(BaseModel):
    """One item from an RSS feed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str = Field(description="Item title")
    link: str = Field(description="Item permalink")
    published: str | None = Field(default=None, description="Publish date text")
    content_html: str = Field(description="Item body HTML (content or description)")


class _MarkdownExtractor(HTMLParser):
    """Convert HTML to compact markdown, preserving anchor hrefs as links."""

    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base_url = base_url
        self._parts: list[str] = []
        self._skip_depth = 0
        self._current_href: str | None = None
        self._link_text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "a":
            href = dict(attrs).get("href")
            self._current_href = urljoin(self._base_url, href) if href else None
            self._link_text_parts = []
        elif tag == "br":
            self._parts.append("\n")
        elif tag == "li":
            self._parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "a":
            link_text = "".join(self._link_text_parts).strip()
            if self._current_href and link_text:
                self._parts.append(f"[{link_text}]({self._current_href})")
            elif link_text:
                self._parts.append(link_text)
            self._current_href = None
            self._link_text_parts = []
        elif tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._current_href is not None:
            self._link_text_parts.append(data)
        else:
            self._parts.append(data)

    def get_markdown(self) -> str:
        joined = "".join(self._parts)
        collapsed_spaces = "\n".join(
            re.sub(r"[ \t]+", " ", line).strip() for line in joined.splitlines()
        )
        return re.sub(r"\n{3,}", "\n\n", collapsed_spaces).strip()


def html_to_markdown(html: str, base_url: str) -> str:
    extractor = _MarkdownExtractor(base_url)
    extractor.feed(html)
    return extractor.get_markdown()


def parse_funcheap_permalinks(listing_html: str, base_url: str) -> list[str]:
    """Extract per-event permalinks from a Funcheap category listing page."""
    matches = re.findall(
        r'entry-title[^>]*>\s*<a[^>]+href="([^"]+)"', listing_html, re.IGNORECASE
    )
    seen: set[str] = set()
    permalinks: list[str] = []
    for raw_href in matches:
        absolute = urljoin(base_url, raw_href)
        # Only individual Funcheap event posts, not category/guide index pages.
        if "/category/" in absolute or "/city-guide/" in absolute:
            continue
        if absolute not in seen:
            seen.add(absolute)
            permalinks.append(absolute)
    return permalinks


def _iter_jsonld_objects(html: str):
    for block in re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        re.IGNORECASE | re.DOTALL,
    ):
        try:
            yield json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue


def _walk_for_events(node: object, found: list[dict]) -> None:
    if isinstance(node, dict):
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(isinstance(entry, str) and entry in _EVENT_LD_TYPES for entry in types):
            found.append(node)
        for value in node.values():
            _walk_for_events(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_for_events(value, found)


def extract_jsonld_events(html: str) -> list[dict]:
    found: list[dict] = []
    for obj in _iter_jsonld_objects(html):
        _walk_for_events(obj, found)
    return found


def _location_fields(location: object) -> tuple[str | None, str | None, str | None]:
    """Return (venue_name, city, street_address) from a JSON-LD location node."""
    if not isinstance(location, dict):
        return None, None, None
    venue_name = location.get("name")
    venue = unescape(venue_name) if isinstance(venue_name, str) else None
    address = location.get("address")
    if isinstance(address, str):
        full_address = unescape(address)
    elif isinstance(address, dict):
        street = address.get("streetAddress") or ""
        locality = address.get("addressLocality") or ""
        region = address.get("addressRegion") or ""
        full_address = (
            ", ".join(part for part in (street, locality, region) if part) or None
        )
    else:
        full_address = None
    # Derive a city from a "street, City, ST" address when present.
    city: str | None = None
    if full_address and "," in full_address:
        pieces = [piece.strip() for piece in full_address.split(",")]
        if len(pieces) >= 2:
            city = pieces[-2] if re.match(r"^[A-Z]{2}\b", pieces[-1]) else pieces[-1]
    return venue, city, full_address


def _price_text(event_ld: dict) -> str | None:
    offers = event_ld.get("offers")
    offer = offers[0] if isinstance(offers, list) and offers else offers
    if isinstance(offer, dict):
        price = offer.get("price")
        if price in (0, "0", "0.00"):
            return "FREE"
        if price:
            return f"${price}"
    return None


def _hour_to_24(hour: int, meridiem: str | None) -> int | None:
    if meridiem is None:
        return None
    is_pm = meridiem.lower().startswith("p")
    if is_pm:
        return hour if hour == 12 else hour + 12
    return 0 if hour == 12 else hour


def _first_prose_time_range(text: str) -> tuple[str, int] | None:
    """Return (matched-text, start-hour-24h) for the first time range in prose."""
    match = _TIME_RANGE_RE.search(text)
    if match is None:
        return None
    start_hour_raw = int(match.group(1))
    start_meridiem = match.group(3) or match.group(6)
    start_hour = _hour_to_24(start_hour_raw, start_meridiem)
    if start_hour is None:
        return None
    matched_text = re.sub(r"\s+", " ", match.group(0)).strip()
    return matched_text, start_hour


def _assess_event_time(
    structured_start_iso: str, structured_time_label: str | None, page_text: str
) -> tuple[TimeConfidence, dict[str, str]]:
    """Compare the structured start time against a prose time range on the page.

    Returns (confidence, candidates). LOW with both candidates when the page
    contradicts itself; HIGH when they agree or there is no prose time; the page
    text is only trusted, never auto-preferred over the structured value.
    """
    if structured_time_label is None:
        return TimeConfidence.UNKNOWN, {}
    prose = _first_prose_time_range(page_text)
    if prose is None:
        return TimeConfidence.HIGH, {}
    prose_text, prose_hour = prose
    try:
        structured_hour = datetime.fromisoformat(structured_start_iso).hour
    except ValueError:
        return TimeConfidence.HIGH, {}
    if abs(structured_hour - prose_hour) >= 1:
        return TimeConfidence.LOW, {
            "structured": structured_time_label,
            "prose": prose_text,
        }
    return TimeConfidence.HIGH, {}


def parse_funcheap_event(
    event_html: str, event_url: str, source: SourceConfig
) -> Event | None:
    """Build an Event from a Funcheap event page's schema.org JSON-LD, or None."""
    events_ld = extract_jsonld_events(event_html)
    if not events_ld:
        return None
    event_ld = events_ld[0]
    start = event_ld.get("startDate")
    if not isinstance(start, str):
        return None
    start_date = parse_iso_date(start)
    if start_date is None:
        return None
    end = event_ld.get("endDate")
    end_date = parse_iso_date(end) if isinstance(end, str) else None
    name = event_ld.get("name")
    title = unescape(name) if isinstance(name, str) else event_url
    venue, city, address = _location_fields(event_ld.get("location"))
    structured_time = human_time_from_iso(start)
    # Trust the event's own page, but never silently pick a side when its
    # structured time and its prose time disagree -- flag it for verification.
    time_confidence, time_candidates = _assess_event_time(
        start, structured_time, html_to_markdown(event_html, event_url)
    )
    return Event(
        source_key=source.key,
        source_name=source.name,
        title=title,
        date=start_date.isoformat(),
        end_date=end_date.isoformat() if end_date else None,
        day=weekday_label(start_date),
        start_time=structured_time,
        time_confidence=time_confidence,
        time_candidates=time_candidates,
        venue=venue,
        city=city,
        address=address,
        price_text=_price_text(event_ld),
        source_url=event_url,
        link_quality=LinkQuality.EVENT,
        raw_excerpt=f"{title} | {venue or city or ''} | {start_date.isoformat()}".strip(
            " |"
        ),
    )


def parse_rss_items(xml_text: str) -> list[RssItem]:
    """Parse RSS <item> entries with a tolerant regex (namespaces vary by feed)."""
    items: list[RssItem] = []
    for block in re.findall(
        r"<item[ >].*?</item>", xml_text, re.DOTALL | re.IGNORECASE
    ):
        title = _first_tag_text(block, "title")
        link = _first_tag_text(block, "link")
        published = _first_tag_text(block, "pubDate")
        content = _first_tag_text(block, "content:encoded") or _first_tag_text(
            block, "description"
        )
        if not title or not link:
            continue
        items.append(
            RssItem(
                title=unescape(title.strip()),
                link=link.strip(),
                published=published.strip() if published else None,
                content_html=content or "",
            )
        )
    return items


def _first_tag_text(block: str, tag: str) -> str | None:
    match = re.search(
        rf"<{re.escape(tag)}[^>]*>(.*?)</{re.escape(tag)}>",
        block,
        re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    inner = match.group(1).strip()
    cdata = re.match(r"^<!\[CDATA\[(.*)\]\]>$", inner, re.DOTALL)
    return cdata.group(1) if cdata else inner
