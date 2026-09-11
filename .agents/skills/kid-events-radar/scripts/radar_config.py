#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["pydantic>=2"]
# ///
"""Defaults, on-disk paths, and the sources-config loader for the radar."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Final

from pydantic import ValidationError
from radar_types import PanelEntry, RadarSources, SourceConfig, SourceConfigError

DEFAULT_HOME_ADDRESS: Final[str] = "San Francisco, CA"
DEFAULT_HOME_DESCRIPTION: Final[str] = "San Francisco"
DEFAULT_HOME_LATITUDE: Final[float] = 37.7749
DEFAULT_HOME_LONGITUDE: Final[float] = -122.4194
DEFAULT_AGE_MIN: Final[int] = 4
DEFAULT_AGE_MAX: Final[int] = 7
DEFAULT_DRIVE_RADIUS_MINUTES: Final[int] = 60
DEFAULT_WEEKS_AHEAD: Final[int] = 8
DEFAULT_MODEL: Final[str] = "claude-haiku-4-5"

# How many past run directories (raw payloads + intermediates) to retain.
RUN_RETENTION_COUNT: Final[int] = 8

# Repo-root-relative data locations. The pipeline always runs from the repo root
# (per CLAUDE.md), so these resolve consistently for both a chat run and cron.
DATA_DIR: Final[Path] = Path("data/.skills/kid-events-radar")
LATEST_SNAPSHOT_PATH: Final[Path] = DATA_DIR / "latest.json"
CACHE_DIR: Final[Path] = DATA_DIR / "cache"
PARSE_CACHE_PATH: Final[Path] = CACHE_DIR / "parse_cache.json"
GEOCACHE_PATH: Final[Path] = CACHE_DIR / "geocache.json"
RUNS_DIR: Final[Path] = DATA_DIR / "runs"
PREFERENCE_PROFILE_PATH: Final[Path] = DATA_DIR / "preference_profile.json"

# The skill's own asset directory (sources config lives here).
_SCRIPTS_DIR: Final[Path] = Path(__file__).resolve().parent
DEFAULT_SOURCES_CONFIG_PATH: Final[Path] = (
    _SCRIPTS_DIR.parent / "assets" / "sources.toml"
)

# Repo root: <root>/.agents/skills/kid-events-radar/scripts -> up four levels.
REPO_ROOT: Final[Path] = _SCRIPTS_DIR.parents[3]
BROWSER_FETCH_HELPER_PATH: Final[Path] = _SCRIPTS_DIR / "browser_fetch.py"

# A real desktop-browser User-Agent -- most family-event sites bot-block a bare
# urllib request but serve a normal-looking one fine.
BROWSER_USER_AGENT: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
FORTRESS_EXECUTABLE_PATH: Final[str] = "/opt/fortress/tilion-fortress/tilion"

# Two-threshold network timeouts (seconds): hard = "definitely broken".
HTTP_HARD_TIMEOUT_SECONDS: Final[float] = 30.0
BROWSER_HARD_TIMEOUT_SECONDS: Final[float] = 90.0


def load_sources_config(config_path: Path) -> RadarSources:
    """Parse the sources TOML into a validated RadarSources, or raise."""
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise SourceConfigError(f"Cannot read sources config: {config_path}") from error
    try:
        parsed = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as error:
        raise SourceConfigError(
            f"Invalid TOML in sources config: {config_path}"
        ) from error

    raw_sources = parsed.get("source", [])
    raw_panel = parsed.get("panel", [])
    if not isinstance(raw_sources, list) or not raw_sources:
        raise SourceConfigError(
            f"Sources config has no [[source]] entries: {config_path}"
        )
    try:
        sources = tuple(SourceConfig.model_validate(entry) for entry in raw_sources)
        panel = tuple(PanelEntry.model_validate(entry) for entry in raw_panel)
    except ValidationError as error:
        raise SourceConfigError(
            f"Sources config failed validation: {config_path}"
        ) from error
    return RadarSources(sources=sources, panel=panel)
