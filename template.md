---
title: "Family Weekend Radar"
description: "A weekly, self-updating radar of fun, kid-appropriate local events near home -- weekend-first with drive times, source links, flags for ticketed events, and a guided setup to point it at your own city."
thumbnail: "template.svg"
version: v1
format: v2
---

# Family Weekend Radar

This file is the manifest for the **Family Weekend Radar** template (slug:
`family-weekend-radar`). It is the one document a future agent reads to understand,
present, and adapt this template. If you are an agent in a mind that was
created from this template, this file is your script: read all of it, then
follow "How to adapt it" below.

## What it is

A weekly, self-updating radar of fun, kid-appropriate local events near home -- weekend-first with drive times, source links, flags for ticketed events, and a guided setup to point it at your own city.

Family Weekend Radar solves a specific parenting problem: the best local kids'
events -- a fire-truck open house, a free museum day, a neighborhood festival --
spread by word of mouth through a network of other parents, and if you do not
have that network you simply miss them. This template stands in for that
network. Once a week it pulls a dozen Bay Area family-event sources, keeps only
the events that fit a child's age and sit within a drivable radius of home,
works out the drive time to each, de-dupes across sources, flags the caveats
(ticketed, fundraiser, registration required, unverified time), and buckets
everything into upcoming weekends plus a Thursday/Friday early-evening slot. The
result is written to a stable snapshot file.

What the user sees is a single web tab -- the "Family Weekend Radar" -- that
reads that snapshot. It opens on this weekend as a wall of cheerful event cards,
each with a colored map-pin badge showing the drive time (green for close, warm
for far), the venue and city, a one-line "why a kid would like this", caveat
badges, and a deep link to the event's own page. A pager steps forward through
the next several weekends so a parent can plan ahead, a "Meh" control
de-emphasizes things they are not interested in, and a "worth a peek yourself"
panel lists the human-only sources (parent Facebook groups, Instagram accounts)
that can't be automated. The page never fetches on load; it just renders the
latest snapshot, and shows a friendly empty state until the first weekly run has
happened -- confirmed by booting the app with no snapshot present at all.

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/kid_events_radar`
- `.agents/skills/kid-events-radar`
- `system/supervisord.conf`
- `pyproject.toml`

There are two moving parts -- a pipeline that produces data and an app that
displays it -- plus the two files that wire the app into the workspace:

- **`.agents/skills/kid-events-radar`** is the pipeline (a crystallized skill).
  Its `run all` entry point fetches the shipped family-event sources
  concurrently (falling back to the bundled stealth browser for JavaScript-heavy
  or bot-blocking pages), extracts events, filters by age and drive radius,
  geocodes each venue and computes drive time from home, curates out adult /
  non-kid events while keeping-and-flagging ticketed and fundraiser ones,
  fuzzy-de-dupes across sources, buckets the survivors into pageable weekends
  plus a Thursday/Friday evening list, and writes the snapshot to
  `data/.skills/kid-events-radar/latest.json`. It also carries a `verify-source`
  subcommand (test a candidate URL before adding it) and a "Setting up for your
  area" guide for adopters outside the Bay Area. The source list lives in
  `assets/sources.toml`, editable with no code change.
- **`system/apps/kid_events_radar`** is the Flask web app -- the "Family Weekend
  Radar" tab. It is a pure read layer over the snapshot: on each request it
  reads `latest.json`, shapes it, and renders the single-page view (weekend
  cards, drive-time pins, the weekend pager, the Thu/Fri evening section, the
  "Meh" de-emphasize control, and the check-yourself panel). It never runs the
  pipeline itself and degrades to a graceful empty state when no snapshot exists
  yet.
- **`system/supervisord.conf`** carries the `kid-events-radar` program entry.
  That program registers the app's port with `system/scripts/forward_port.py`
  (`--url http://localhost:8080 --name kid-events-radar --icon-file
  system/apps/kid_events_radar/icon.svg --program kid-events-radar`) and then
  runs `uv run kid-events-radar`, so the tab is served on port 8080 and
  supervised like every other app. It starts automatically (`autostart=true`):
  the app needs no external accounts or secrets, and renders a friendly empty
  state until the first weekly run.
- **`pyproject.toml`** carries `kid_events_radar` as a workspace package (and its
  `kid-events-radar` entry-point script), so `uv run kid-events-radar` resolves.

At runtime the two halves meet only at the snapshot file: the pipeline writes
it (on demand or on a weekly schedule the adopter sets up), and the app reads it.
Nothing connects them directly, which is why the app stays fast and the pipeline
can run unattended.

## Recipe

This template is version `v1`. It is not a fork of the
workspace it came from -- it is DERIVED from it by a recipe: include these
paths, leave these out, apply these published-version rules. An update re-runs
the recipe against the current workspace and publishes the result as the next
version, so anything excluded stays excluded even though it still exists in the
source workspace.

The recipe is machine-read, so it lives in the sibling
[`template.toml`](template.toml) -- its `[recipe]` table -- along with
the structured requirements and the environment this template needs
installed. That file is authoritative for all of it; this one holds the prose.

## Requirements

Everything the adopting mind must deal with before this template is really
theirs. Two kinds of entry, handled at different times:

- **Activation** -- what must be SET UP before anything runs, in the
  machine-readable `requires_` forms below. The adopting agent acts on these
  ITSELF, first, before asking anything.
- **Adaptation** -- what must be DECIDED or REWIRED, in prose. Worked through
  interactively with the user, after activation.

**Activation** -- what the adopting agent sets up before anything runs:

- requires_llm: the pipeline calls Claude to extract and curate events. It is
  written for the KEYLESS subscription path (`claude -p`, via
  `scripts/claude_p.py`) -- no API key needed. An adopter on the keyed path
  (`ANTHROPIC_API_KEY` / litellm) can switch the two model call sites
  (extraction and curation) per the `use-ai-integration` skill. A full weekly
  run costs roughly $0.65-0.85 on the keyless path.

No external permissions or secrets are required. Every automatable source is
fetched anonymously over plain HTTP (with the base image's bundled Fortress /
Chromium browser as a fallback for JavaScript-heavy or bot-blocking pages), and
geocoding and drive-time use free public services (Nominatim and OSRM) that
need no key. So there are no `requires_permission` or `requires_secret` lines.

**Adaptation** -- what the adopter should decide or rewire, interactively:

- **Home address.** The default home ships as a neutral "San Francisco, CA"
  city-center point. The adopter should set it to their actual home address
  (and coordinates) -- either by passing `--home-address` / `--home-description`
  / `--home-coords` on the run, or by editing the defaults in
  `scripts/radar_config.py` so scheduled runs use them without flags. The
  coordinates are what drive-time is measured from, so they matter.
- **Age and radius.** The defaults are ages 4-7 within a 60-minute drive. An
  adopter with different-aged children, or in a denser or sparser area, should
  adjust `--age-min` / `--age-max` and `--drive-radius-min` (or the config
  defaults).
- **Source list (only outside the Bay Area).** The shipped `assets/sources.toml`
  is an SF Bay Area list, so a Bay Area adopter is turnkey. An adopter elsewhere
  must rebuild it for their metro following the skill's "Setting up for your
  area" section: web-search their city for family-event aggregators, parent
  blogs, library / parks calendars, and museum/zoo pages, run each candidate
  through the skill's `verify-source` subcommand, and keep the ~5-8 that pass.
  The static "check these yourself" panel entries should likewise be swapped for
  local parent groups.
- **Weekly schedule.** The pipeline is headless and meant to run on a cadence,
  but the template does not ship a cron entry. The adopter should schedule
  `run all` (e.g. a Monday-morning job) via the `manage-scheduled-tasks` skill.
- **"Meh" preference persistence.** The web view's "Meh" de-emphasize control is
  client-side only today (it lives in the page for the current session and does
  not yet write back to the pipeline's `preference_profile.json`). An adopter
  who wants those preferences to persist across weekly runs would need to wire
  the control to that profile file.

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

Nothing extra -- runs on the stock workspace environment. The app and pipeline
are pure Python (Flask plus the standard library and the repo's existing
dependencies), and the JavaScript-heavy sources are fetched with the base
image's bundled Fortress / Chromium browser, so there are no additional apt,
npm, uv, or cargo installs. Geocoding and routing use the free public Nominatim
and OSRM services over the network, which need nothing installed.

## How to adapt it

Instructions for the NEXT agent -- the one adapting this template into a
new mind. This is the `use-template` skill's template path; in short:

1. Read this entire file first, especially "Requirements" below. It holds two
   kinds of entry and they are handled at different times: the machine-readable
   `requires_` lines are ACTIVATION (set them up before anything runs), and
   the prose bullets are ADAPTATION (decide or rewire them afterwards).
2. Present the template to the user in plain, non-technical language: what
   it is, what it does, and what it needs from them (name the activation
   requirements).
3. Ask whether they want to use the same connectors (e.g. their own Slack).
   If YES: ACTIVATE FIRST -- initiate every `requires_permission` line NOW
   via a latchkey permission request (see the `latchkey` skill; the request
   opens the approval/login flow in the minds app), wire up any
   `requires_secret` values, start the services, and get the app showing
   THE USER'S OWN DATA. Done for a data-backed app means the user can open it
   and see their own data -- NOT that a service starts or an endpoint returns
   200. Then tell them it is live and to take a look.
4. Only AFTER that (or immediately, if they chose different connectors -- the
   swap is then the first adaptation) ask: "How do you want to adapt it?"
5. Work through each requirement interactively, one at a time. Translate each
   into plain language, ask for a decision only when you genuinely need one,
   and resolve the obvious ones yourself.
6. When done, append a dated entry to "Adaptation history" below (never
   rewrite earlier entries) and commit.

## Publication history

This template's changelog: what each published version changed. The PUBLISHER
appends one entry per version (newest last); earlier entries are never rewritten.
This is distinct from "Adaptation history" below, which is the ADOPTERS' log.

### v1 (2026-09-10) -- first release on the imbue-ai fork: the kid-events pipeline skill plus the Family Weekend Radar web app, re-cut onto minds-v0.5.0 with the app autostarting (no external permissions needed) and a neutral San Francisco default home plus a guided setup for adapting the source list to any city.

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
