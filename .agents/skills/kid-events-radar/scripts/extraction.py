#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Model extraction for irregular sources (ai_html / ai_rss), via keyless claude -p.

Used only for sources with no structured format to parse deterministically. The
same prompt/criteria run every time, only the page content varies -- an
``[ai-script]`` step. Output is deliberately compact JSON to bound cost.
"""

from __future__ import annotations

import json
import re
from datetime import date

from claude_p import ClaudeCLIError, claude_p_completion
from parsing import html_to_markdown, parse_rss_items
from pydantic import BaseModel, ConfigDict, Field
from radar_dates import parse_iso_date, weekday_label
from radar_types import Event, LinkQuality, ParseError, SourceConfig, TimeConfidence

# Bound the text handed to the model so a huge page can't blow up cost/latency.
_MAX_MARKDOWN_CHARS = 14000
_MAX_RSS_ITEMS = 4
_MAX_RSS_ITEM_CHARS = 3500
_MAX_EVENTS_PER_SOURCE = 40

_EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured children's-event listings from web page text. "
    "You output ONLY a JSON array and never any prose or code fences."
)


class ExtractionResult(BaseModel):
    """Events extracted from one source, plus what the model call cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[Event, ...] = Field(description="Extracted candidate events")
    cost_usd: float = Field(description="Cost of the model call in USD")


def _prepare_html_text(raw_content: str, base_url: str) -> str:
    return html_to_markdown(raw_content, base_url)[:_MAX_MARKDOWN_CHARS]


def _prepare_rss_text(raw_content: str) -> str:
    items = parse_rss_items(raw_content)[:_MAX_RSS_ITEMS]
    sections: list[str] = []
    for item in items:
        body = html_to_markdown(item.content_html, item.link)[:_MAX_RSS_ITEM_CHARS]
        sections.append(f"## {item.title}\n(source link: {item.link})\n{body}")
    return "\n\n".join(sections)


def _build_prompt(
    source_text: str, *, today: date, window_end: date, age_min: int, age_max: int
) -> str:
    return (
        f"Today is {today.isoformat()}. Extract every listed event suitable for "
        f"children ages {age_min}-{age_max} that has a concrete calendar date on or "
        f"after {today.isoformat()} and on or before {window_end.isoformat()}.\n\n"
        "Return a JSON array. Each element must be an object with EXACTLY these keys:\n"
        '  "title": string,\n'
        '  "date": "YYYY-MM-DD" (the single start date; pick the next occurrence if recurring),\n'
        '  "start_time": string like "10:00 AM" or null,\n'
        '  "time_confidence": "high" if a single clear time (or agreeing times), '
        '"low" if the text shows two DIFFERENT times for the same event, "unknown" '
        "if no time is given,\n"
        '  "time_candidates": when time_confidence is "low", an object mapping a '
        'label to each conflicting time, e.g. {"listing": "12pm", "detail": "7pm"}; '
        "otherwise an empty object. Never silently pick one time when they conflict.\n"
        '  "venue": string or null,\n'
        '  "city": string or null,\n'
        '  "source_url": the event\'s OWN link taken from the markdown links in the '
        "text (the per-event page or the venue/organizer page), or null if none is present,\n"
        '  "price_text": string like "FREE" or "$44+" or null,\n'
        '  "raw_excerpt": a short (<200 char) verbatim snippet from the text.\n\n'
        "Rules: only include events with a concrete date in the window. Do NOT invent "
        "URLs -- use only links present in the text. Output ONLY the JSON array, no "
        f"prose. Return at most {_MAX_EVENTS_PER_SOURCE} events.\n\n"
        f"PAGE TEXT:\n{source_text}"
    )


def _strip_to_json_array(text: str) -> str:
    without_fences = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    start = without_fences.find("[")
    end = without_fences.rfind("]")
    if start == -1 or end == -1 or end < start:
        raise ParseError("model output contained no JSON array")
    return without_fences[start : end + 1]


def _coerce_event(
    raw: dict, source: SourceConfig, *, today: date, window_end: date
) -> Event | None:
    if not isinstance(raw, dict):
        return None
    raw_date = raw.get("date")
    parsed_date = parse_iso_date(raw_date) if isinstance(raw_date, str) else None
    if parsed_date is None or parsed_date < today or parsed_date > window_end:
        return None
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    raw_url = raw.get("source_url")
    has_event_url = isinstance(raw_url, str) and raw_url.startswith("http")
    source_url = raw_url if has_event_url else source.url
    excerpt = raw.get("raw_excerpt")
    start_time = _optional_str(raw.get("start_time"))
    time_confidence, time_candidates = _coerce_time_confidence(raw, start_time)
    return Event(
        source_key=source.key,
        source_name=source.name,
        title=title.strip(),
        date=parsed_date.isoformat(),
        day=weekday_label(parsed_date),
        start_time=start_time,
        time_confidence=time_confidence,
        time_candidates=time_candidates,
        venue=_optional_str(raw.get("venue")),
        city=_optional_str(raw.get("city")),
        price_text=_optional_str(raw.get("price_text")),
        source_url=source_url,
        link_quality=LinkQuality.EVENT if has_event_url else LinkQuality.LANDING,
        raw_excerpt=(
            excerpt.strip()[:200] if isinstance(excerpt, str) else title.strip()
        ),
    )


def _coerce_time_confidence(
    raw: dict, start_time: str | None
) -> tuple[TimeConfidence, dict[str, str]]:
    raw_confidence = raw.get("time_confidence")
    raw_candidates = raw.get("time_candidates")
    candidates: dict[str, str] = {}
    if isinstance(raw_candidates, dict):
        candidates = {
            str(label): str(value)
            for label, value in raw_candidates.items()
            if isinstance(value, str) and value.strip()
        }
    if raw_confidence == "low" and len(candidates) >= 2:
        return TimeConfidence.LOW, candidates
    if raw_confidence == "high" and start_time:
        return TimeConfidence.HIGH, {}
    if start_time:
        return TimeConfidence.HIGH, {}
    return TimeConfidence.UNKNOWN, {}


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip() and value.strip().lower() != "null":
        return value.strip()
    return None


def extract_events(
    raw_content: str,
    source: SourceConfig,
    *,
    today: date,
    window_end: date,
    age_min: int,
    age_max: int,
    model: str,
) -> ExtractionResult:
    """Extract candidate events from an irregular source via one model call."""
    if source.parser.value == "ai_rss":
        source_text = _prepare_rss_text(raw_content)
    else:
        source_text = _prepare_html_text(raw_content, source.url)
    if not source_text.strip():
        return ExtractionResult(events=(), cost_usd=0.0)

    prompt = _build_prompt(
        source_text,
        today=today,
        window_end=window_end,
        age_min=age_min,
        age_max=age_max,
    )
    try:
        result = claude_p_completion(
            prompt, system=_EXTRACTION_SYSTEM_PROMPT, model=model
        )
    except ClaudeCLIError as error:
        raise ParseError(
            f"extraction model call failed for {source.key}: {error}"
        ) from error

    array_text = _strip_to_json_array(result.text)
    try:
        raw_events = json.loads(array_text)
    except (json.JSONDecodeError, ValueError) as error:
        raise ParseError(
            f"extraction JSON was invalid for {source.key}: {error}"
        ) from error
    if not isinstance(raw_events, list):
        raise ParseError(f"extraction JSON was not an array for {source.key}")

    coerced = [
        event
        for raw in raw_events[:_MAX_EVENTS_PER_SOURCE]
        if (event := _coerce_event(raw, source, today=today, window_end=window_end))
        is not None
    ]
    return ExtractionResult(events=tuple(coerced), cost_usd=result.cost_usd)
