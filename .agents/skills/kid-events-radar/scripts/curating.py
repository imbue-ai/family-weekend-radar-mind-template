#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Batched model curation: verify kid-attendability, add a fit note, flag caveats.

Curation runs in two layers. First a deterministic guardrail drops the clear-cut
adult / alcohol-centric / pure-transaction classes the user never wants to see
(beer & wine gardens, bar crawls, garage & estate sales, adult lectures & history
talks, adult service/health fairs, drag-show block parties, adult civic
cleanups). Then one model call per batch judges the rest against the boundary
"would a parent plausibly bring a 4-7-year-old TO this as an activity?" -- it
KEEPS free/low-cost community and cultural events with any family appeal (and sets
a short ``flag`` caveat on paid/ticketed/registration/fundraiser ones) and DROPS
adult-only or pure-transaction events the guardrail's keywords missed. Past-dated
and out-of-radius filtering is handled deterministically elsewhere.
"""

from __future__ import annotations

import json
import re
import sys

from claude_p import ClaudeCLIError, claude_p_completion
from pydantic import BaseModel, ConfigDict, Field
from radar_types import Event

_CURATION_BATCH_SIZE = 40
_CURATION_SYSTEM_PROMPT = (
    "You are a careful curator of children's events for a parent of a young "
    "child (roughly age 4-7). The bar is a single question: would a parent "
    "plausibly bring a young child TO this as an activity? Free or low-cost "
    "community and cultural events with any family appeal are IN, even when not "
    "exclusively for kids. Alcohol-centric, adults-only, and pure-transaction "
    "events are OUT. You write a one-line fit note and flag caveats. You output "
    "ONLY a JSON array, no prose."
)

# Deterministic guardrail. Each pattern below marks an event the user has
# confirmed should never appear, so we drop it before the model batch (locking
# the decision in and saving a model call). Two tiers:
#   * _ALWAYS_DROP_PATTERNS -- inherently adult; a family-friendly word in the
#     title cannot rescue them (21+, adults-only, drag shows, bar/pub crawls).
#   * _OVERRIDABLE_DROP_PATTERNS -- adult/transactional by default, but spared
#     when the title also advertises a kids'/family activity (e.g. a festival
#     that merely mentions a beer garden alongside a kids' zone). This is what
#     lets a "family flea market" or "beach cleanup + kids' crafts" survive
#     while a plain garage sale or adult beer garden drops.
_ALWAYS_DROP_PATTERNS: tuple[str, ...] = (
    r"\b21\s*\+",
    r"\b18\s*\+",
    r"\badults?[\s-]*only\b",
    r"\bdrag\s+show",
    r"\bdrag\s+brunch",
    r"\bbar\s+crawl\b",
    r"\bpub\s+crawl\b",
)
_OVERRIDABLE_DROP_PATTERNS: tuple[str, ...] = (
    # Alcohol-centric ("beer" excludes root/ginger beer).
    r"(?<!root )(?<!ginger )\bbeer\b",
    r"\bwine\s+(?:garden|tasting|walk)\b",
    r"\bbeer\s+garden\b",
    r"\bbeer\s*/\s*wine\b",
    r"\bwine\s*/\s*beer\b",
    r"\bbrewery\b",
    r"\bwinery\b",
    # Pure-transaction sales (a flea/second-hand market is left to the model,
    # which keeps it only when it advertises kids'/family activities).
    r"\bgarage\s+sale\b",
    r"\byard\s+sale\b",
    r"\bestate\s+sale\b",
    r"\brummage\s+sale\b",
    # Adult civic cleanups and adult service/health fairs.
    r"\btrash\s+cleanup\b",
    r"\bneighborhood\s+cleanup\b",
    r"\bhealth\s+fair\b",
    r"\bhealth\s+access\b",
    r"\bjob\s+fair\b",
    r"\bcareer\s+fair\b",
    r"\bresource\s+fair\b",
    r"\bblood\s+drive\b",
    # Adult lectures / talks.
    r"\bhistory\s+talk\b",
    r"\blecture\b",
    r"\btalk\s+by\b",
    r"\bpanel\s+discussion\b",
)
# Words that mark an event as genuinely aimed at kids/families, which spare it
# from the _OVERRIDABLE_DROP_PATTERNS tier.
_KID_SIGNAL_PATTERN = re.compile(
    r"\b(kid|kids|child|children|family|families|toddler|toddlers|baby|babies|"
    r"preschool|storytime|story\s+time|all\s+ages)\b",
    re.IGNORECASE,
)
_ALWAYS_DROP_RE = [re.compile(pattern, re.IGNORECASE) for pattern in _ALWAYS_DROP_PATTERNS]
_OVERRIDABLE_DROP_RE = [
    re.compile(pattern, re.IGNORECASE) for pattern in _OVERRIDABLE_DROP_PATTERNS
]


def is_clearly_not_kid_event(title: str) -> bool:
    """True for titles in a confirmed adult/alcohol/pure-transaction class.

    Deterministic guardrail applied before the model pass. A kids'/family signal
    in the title spares the overridable (alcohol/transaction/civic/lecture) tier
    but never the always-drop tier (21+, adults-only, drag shows, bar crawls).
    """
    if any(pattern.search(title) for pattern in _ALWAYS_DROP_RE):
        return True
    if _KID_SIGNAL_PATTERN.search(title):
        return False
    return any(pattern.search(title) for pattern in _OVERRIDABLE_DROP_RE)


class CurationResult(BaseModel):
    """Curated events (dropped ones removed) plus total model cost."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    events: tuple[Event, ...] = Field(description="Kept, curated events")
    dropped_count: int = Field(description="How many candidates were dropped")
    cost_usd: float = Field(description="Total model cost in USD")


def _build_prompt(batch: list[Event], *, age_min: int, age_max: int) -> str:
    candidates = [
        {
            "index": index,
            "title": event.title,
            "venue": event.venue,
            "city": event.city,
            "date": event.date,
            "price_text": event.price_text,
            "source": event.source_name,
        }
        for index, event in enumerate(batch)
    ]
    return (
        f"Judge each candidate event for a family with a child aged {age_min}-{age_max}.\n\n"
        "THE TEST for keep vs drop: would a parent plausibly bring a young child "
        "TO this as an activity?\n"
        "  KEEP (keep=true): free or low-cost community and cultural events with any "
        "family appeal, even when not exclusively for kids -- e.g. community-garden "
        "anniversaries and potlucks, sand-sculpture contests, art-center birthday "
        "celebrations, festivals, fairs, bubble events, family days, open houses, "
        "storytimes, parades. Also KEEP paid, ticketed, registration-required, and "
        "fundraiser events (a family can still attend those) -- do not drop them, "
        "just note the catch in the flag.\n"
        "  DROP (keep=false): events a parent would not bring a young child to as an "
        "activity -- anything alcohol-centric (beer or wine gardens, bar/pub crawls, "
        'tastings, "$1 beer" events); adults-only / 21+; drag-show or adult block '
        "parties; adult civic cleanups; garage / estate / yard sales that are just "
        "selling with no kids' program; adult lectures, history talks, or panel "
        "discussions; and adult service or health/job/resource fairs. A second-hand / "
        "flea market is KEEP only if it advertises kids'/family activities; a plain "
        "sale is DROP.\n\n"
        "Return a JSON array with one object per candidate, keyed by its index:\n"
        '  "index": integer matching the candidate,\n'
        '  "keep": boolean per THE TEST above,\n'
        '  "kid_fit": one sentence on how well it fits the age range,\n'
        '  "flag": a SHORT caveat badge string when there is a catch -- e.g. '
        '"$44+ · ticketed fundraiser", "registration required", "fundraiser/charity '
        'event", "ticketed" -- or null for a free drop-in event with no catch,\n'
        '  "event_type": one lowercase category word, e.g. "festival", "market", '
        '"museum", "film", "fair", "concert", "storytime", "farm", "parade", "class".\n\n'
        "Output ONLY the JSON array.\n\n"
        f"CANDIDATES:\n{json.dumps(candidates, indent=1)}"
    )


def _strip_to_json_array(text: str) -> str | None:
    without_fences = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE)
    start = without_fences.find("[")
    end = without_fences.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    return without_fences[start : end + 1]


def _apply_judgement(event: Event, judgement: dict) -> Event | None:
    """Return the curated event, or None if the model judged it not kid-attendable."""
    if judgement.get("keep") is False:
        return None
    kid_fit = judgement.get("kid_fit")
    flag = judgement.get("flag")
    event_type = judgement.get("event_type")
    return event.model_copy(
        update={
            "age_is_appropriate": True,
            "kid_fit": kid_fit.strip()
            if isinstance(kid_fit, str) and kid_fit.strip()
            else None,
            "flag": flag.strip()
            if isinstance(flag, str) and flag.strip() and flag.strip().lower() != "null"
            else None,
            "event_type": event_type.strip().lower()
            if isinstance(event_type, str) and event_type.strip()
            else None,
        }
    )


def _curate_batch(
    batch: list[Event], *, age_min: int, age_max: int, model: str
) -> tuple[list[Event], int, float]:
    prompt = _build_prompt(batch, age_min=age_min, age_max=age_max)
    try:
        result = claude_p_completion(
            prompt, system=_CURATION_SYSTEM_PROMPT, model=model
        )
    except ClaudeCLIError as error:
        # If curation fails, keep the batch uncurated rather than losing events;
        # they simply carry no kid_fit/flag/event_type.
        print(
            f"[curate] batch model call failed ({error}); keeping batch uncurated",
            file=sys.stderr,
        )
        return list(batch), 0, 0.0

    array_text = _strip_to_json_array(result.text)
    if array_text is None:
        print(
            "[curate] model output had no JSON array; keeping batch uncurated",
            file=sys.stderr,
        )
        return list(batch), 0, result.cost_usd
    try:
        judgements = json.loads(array_text)
    except (json.JSONDecodeError, ValueError):
        print(
            "[curate] model JSON was invalid; keeping batch uncurated", file=sys.stderr
        )
        return list(batch), 0, result.cost_usd

    judgement_by_index = {
        entry["index"]: entry
        for entry in judgements
        if isinstance(entry, dict) and isinstance(entry.get("index"), int)
    }
    kept: list[Event] = []
    dropped = 0
    for index, event in enumerate(batch):
        judgement = judgement_by_index.get(index)
        if judgement is None:
            # No verdict returned -- keep the event uncurated (don't silently drop).
            kept.append(event)
            continue
        curated = _apply_judgement(event, judgement)
        if curated is None:
            dropped += 1
        else:
            kept.append(curated)
    return kept, dropped, result.cost_usd


def curate_events(
    events: list[Event], *, age_min: int, age_max: int, model: str
) -> CurationResult:
    """Curate all candidate events in batches; return kept events and total cost."""
    kept_all: list[Event] = []
    dropped_total = 0
    cost_total = 0.0
    # Deterministic guardrail first: drop the confirmed adult/alcohol/transaction
    # classes before spending a model call on them.
    to_model = [event for event in events if not is_clearly_not_kid_event(event.title)]
    dropped_total += len(events) - len(to_model)
    for start in range(0, len(to_model), _CURATION_BATCH_SIZE):
        batch = to_model[start : start + _CURATION_BATCH_SIZE]
        kept, dropped, cost = _curate_batch(
            batch, age_min=age_min, age_max=age_max, model=model
        )
        kept_all.extend(kept)
        dropped_total += dropped
        cost_total += cost
    return CurationResult(
        events=tuple(kept_all), dropped_count=dropped_total, cost_usd=cost_total
    )
