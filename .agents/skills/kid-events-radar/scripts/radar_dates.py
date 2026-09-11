#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Pure date/time helpers: parsing, weekday labels, windows, weekend grouping."""

from __future__ import annotations

from datetime import date, datetime, timedelta

_WEEKDAY_LABELS = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_MONTH_ABBREVIATIONS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def parse_iso_date(value: str) -> date | None:
    """Parse a YYYY-MM-DD (optionally with a time/zone suffix) into a date."""
    text = value.strip()
    if not text:
        return None
    # Accept a full ISO datetime by taking the date portion.
    date_part = text.split("T")[0].split(" ")[0]
    try:
        return date.fromisoformat(date_part)
    except ValueError:
        return None


def weekday_label(day: date) -> str:
    return _WEEKDAY_LABELS[day.weekday()]


def human_time_from_iso(value: str) -> str | None:
    """Extract a human start time (e.g. '11:00 AM') from an ISO datetime string."""
    text = value.strip()
    if "T" not in text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    hour_12 = parsed.hour % 12 or 12
    meridiem = "AM" if parsed.hour < 12 else "PM"
    if parsed.minute:
        return f"{hour_12}:{parsed.minute:02d} {meridiem}"
    return f"{hour_12}:00 {meridiem}"


def compute_window_end(today: date, weeks_ahead: int) -> date:
    return today + timedelta(weeks=weeks_ahead)


def saturday_of_weekend(day: date) -> date:
    """Return the Saturday that anchors the weekend a given date belongs to.

    Saturday and Sunday map to that Saturday; a Friday maps to the next day's
    Saturday so Thu/Fri handling elsewhere stays separate from weekend grouping.
    """
    weekday = day.weekday()  # Mon=0 .. Sun=6
    if weekday == 5:  # Saturday
        return day
    if weekday == 6:  # Sunday
        return day - timedelta(days=1)
    # Any weekday: advance to the upcoming Saturday.
    return day + timedelta(days=(5 - weekday) % 7)


def weekend_label(saturday: date) -> str:
    """Human label for a weekend, e.g. 'Aug 22-23' or 'Aug 30-31'."""
    sunday = saturday + timedelta(days=1)
    month = _MONTH_ABBREVIATIONS[saturday.month - 1]
    if saturday.month == sunday.month:
        return f"{month} {saturday.day}-{sunday.day}"
    sunday_month = _MONTH_ABBREVIATIONS[sunday.month - 1]
    return f"{month} {saturday.day}-{sunday_month} {sunday.day}"


def is_weekend(day: date) -> bool:
    return day.weekday() in (5, 6)


def is_thursday_or_friday(day: date) -> bool:
    return day.weekday() in (3, 4)
