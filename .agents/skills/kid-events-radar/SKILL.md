---
name: kid-events-radar
description: >-
  Build a "radar" of fun, time-bound kids' events near a home address. Fetches
  family-event sources, keeps age-appropriate events within a drive radius,
  computes drive time from home, de-dupes, flags ticketed/fundraiser/registration
  caveats, buckets into upcoming weekends (pageable) plus Thursday/Friday
  early-evening, and writes a stable JSON snapshot a web view can render. Use when
  someone wants to stop missing local kid events for lack of a parent network, or
  to (re)generate/refresh that snapshot on a schedule.
metadata:
  crystallized: true
---

# kid-events-radar

Turns a set of Bay Area family-event sources into one ranked, deduped snapshot
of kid-appropriate events near a home address -- grouped by weekend so a viewer
can plan several weekends ahead, plus a Thursday/Friday early-evening bucket and
a "check these yourself" panel for sources that can't be automated.

The pipeline is fully headless (no interactive step), so `run all` can be
scheduled (e.g. a weekly Monday-morning cron). It runs **keyless** -- model steps
use the local `claude -p` path -- and depends only on free services (Nominatim
for geocoding, OSRM for drive time). It writes a **stable snapshot**
(`data/.skills/kid-events-radar/latest.json`, overwriting the prior one); a web
view reads that file and never runs the pipeline on page load.

## Inputs (CLI args, all optional -- defaults target the configured home)

- `--home-address` / `--home-description` / `--home-coords "lat,lon"` -- home,
  default `San Francisco, CA` at `37.7749,-122.4194`.
- `--age-min` / `--age-max` -- default `4` / `7`.
- `--drive-radius-min` -- default `60` (intentionally wide, to reach beyond SF).
- `--weeks-ahead` -- default `8` (how many weekends ahead the window spans).
- `--model` -- extraction/curation model, default `claude-haiku-4-5`.
- `--today` -- override "today" (YYYY-MM-DD) for testing.
- `--out` -- snapshot path (assemble / run all), default `latest.json` above.
- `--preference-profile` -- optional dislikes profile (assemble / run all),
  default `data/.skills/kid-events-radar/preference_profile.json`.
- `--sources-config` -- default the skill's `assets/sources.toml`.
- `--run-dir` -- where per-stage intermediates land, default
  `data/.skills/kid-events-radar/runs/current`.

The source list lives in `assets/sources.toml` (each entry: `key`, `name`,
`url`, `parser`, optional `needs_browser`) plus the static check-yourself panel.
Add or remove a source by editing that file -- no code change.

## Setting up for your area

The shipped defaults target the **SF Bay Area**. Adapt the skill to a new home
in this order:

1. **Set the home + filters.** Point the radar at the new home by passing the
   flags on `run all` (or changing the defaults in `scripts/radar_config.py` so
   scheduled runs use them without flags): `--home-address` / `--home-description`
   / `--home-coords "lat,lon"`, `--age-min` / `--age-max`, and `--drive-radius-min`.
   The coords are what geocoding and drive-time are measured from, so set them.

2. **Decide sources.**
   - **In the SF Bay Area: do nothing.** Keep the shipped `assets/sources.toml`
     as-is -- it is turnkey.
   - **Elsewhere: replace `assets/sources.toml`.** You (the adopting agent) are a
     capable web searcher, so *find* the candidates yourself and let the skill
     *verify* them. Web-search that metro for: family-event aggregators, local
     parent blogs / "-Mommies"/"-Families" sites, library and parks-&-rec event
     calendars, children's museums and zoos, and the city's official "things to
     do with kids" page. For each candidate URL, run:

     ```
     uv run .agents/skills/kid-events-radar/scripts/run.py verify-source \
       --url "<candidate URL>" --city "Austin, TX"
     ```

     It fetches the page (using the stealth browser if the page bot-blocks or
     renders via JavaScript), runs the same extraction the pipeline uses, and
     prints a verdict: how many dated, kid-appropriate events it found in the
     window, a few example titles + dates, whether it needed the browser, and the
     `parser`/`needs_browser` to use. It **exits 0 when the source is usable** and
     non-zero when it is a dead end, so you can tell good candidates from bad.

     Keep the sources that pass (aim for ~5-8) and write them into
     `assets/sources.toml` using the verdict's recommended type -- the mapping is:
     `rss` -> `parser = "ai_rss"`, `html` -> `parser = "ai_html"`,
     `html+browser` -> `parser = "ai_html"` with `needs_browser = true`. (The
     `funcheap` parser is a bespoke scraper for `sf.funcheap.com` only -- do not
     use it for other sites.) A usable verdict prints a ready-to-paste
     `[[source]]` block; just give it a real `key` and `name`. Note the candidates
     that failed so the choice is auditable.

     Also update the `[[panel]]` entries (the "check these yourself" sources that
     can't be automated -- local parent Facebook groups, members-only forums,
     Instagram accounts) for the new metro, or remove the Bay Area ones.

3. **Quality varies by city.** It is better to ship **fewer, verified** sources
   than many unverified ones -- a source that `verify-source` rejects will only
   add fetch failures and noise. Five solid, verified sources make a good radar.

## Output

`latest.json` (a `RadarOutput`): metadata (home, age filter, radius, window,
`fetched_at`, per-source fetch status), `weekends` (a list of `{weekend_of,
label, events[]}` groups, ascending so a viewer pages forward), `thu_fri_evening`
(a ranked list), and `check_yourself_panel`. Each event carries `title`, `date`,
`day`, `start_time`, `time_confidence`, `time_candidates`, `venue`, `city`,
`drive_minutes`, `kid_fit`, `flag` (caveat badge or null), `event_type`, `area`,
`source_name`, `source_url` (a per-event deep link), `link_quality`, and
`raw_excerpt`. Raw source payloads are preserved under the run directory for
re-derivation and "view original".

**Time trustworthiness.** Times come from the event's own page (the deep link),
not an aggregator's summary line. When a page contradicts itself (a structured
header time vs a prose time) or two sources disagree, the radar does **not**
silently pick one: it sets `time_confidence: "low"` and records **both** in
`time_candidates` (e.g. `{"structured": "7:00 PM", "prose": "12 to 5 p.m."}`) so
the UI can show a subtle "verify time" hint and display both. A single
unambiguous time is `"high"`; no time is `"unknown"`. Events are never dropped
over a time conflict.

## Flow (one subcommand per stage; `run all` chains them)

1. **`fetch`** `[script]` -- fetch each source's page/feed concurrently with a
   browser User-Agent; fall back to the Fortress browser for sources marked
   `needs_browser`, that bot-block, or whose HTTP response is a JavaScript shell
   (little text / no dates -- detected automatically and re-fetched rendered). A
   failed source is recorded and skipped, never fatal. Raw payloads are saved
   durably.
2. **`extract`** `[script]` + `[ai-script]` -- deterministic parsers for
   structured sources (Funcheap: listing -> per-event permalinks -> schema.org
   JSON-LD; RSS feeds via a feed parse), and a model extraction only for
   irregular HTML/newsletter sources. A source whose fetched content hash is
   unchanged since last run is **reused from the cache** (no re-parse, no model
   call) -- the main cost saver.
3. **`locate`** `[script]` -- geocode each unique venue (Nominatim, cached) and
   compute drive minutes from home (OSRM). Unresolvable venues are kept as
   "location TBD" and ranked last, not dropped.
4. **`curate`** `[script]` + `[ai-script]` -- a deterministic guardrail first
   drops the confirmed adult / alcohol-centric / pure-transaction classes
   (beer & wine gardens, bar crawls, garage & estate sales, adult lectures &
   history talks, adult service/health fairs, drag-show block parties, adult
   civic cleanups), sparing anything whose title advertises a kids'/family
   activity; then one batched model pass judges the rest against the boundary
   "would a parent plausibly bring a 4-7-year-old TO this as an activity?" --
   it KEEPS free/low-cost community and cultural events with any family appeal,
   writes a one-line `kid_fit`, sets a short `flag` on caveated events
   (paid/ticketed, registration, fundraiser) while leaving free drop-ins
   unflagged, tags an `event_type`, and DROPS adult-only or pure-transaction
   events the guardrail's keywords missed.
5. **`assemble`** `[script]` -- **fuzzy de-dupe** across sources (collapse
   events that share a date, a compatible venue/city, and a normalized/similar
   title -- so "8th Annual Crab Cove Fish Festival (Alameda)" and "Crab Cove
   Fish Festival" merge into one, keeping the better-data record), drop
   past-dated and out-of-radius events, bucket into weekends vs Thu/Fri-evening,
   **rank** by drive time (ascending) with an optional preference-profile
   de-emphasis, write the snapshot, and prune old run directories to a bounded
   retention.

Every step is scriptable; there is **no `[prose]` step** -- the flow is meant to
run unattended and be scheduled.

## Invocation

Headless / scheduled (the canonical entry point):

```
uv run .agents/skills/kid-events-radar/scripts/run.py run all
```

It exits non-zero on failure (so a scheduled run can be retried) and overwrites
`latest.json` on success. To drive the stages one at a time (e.g. for a live
progress view), pass a shared `--run-dir` and run `fetch`, `extract`, `locate`,
`curate`, `assemble` in order.

## Preference profile (optional ranking adjustment)

If a preference profile exists at `--preference-profile`, `assemble` reads it and
sinks de-emphasized events toward the bottom of each bucket **without removing
them** (they stay visible). Absent the profile, ranking is pure drive-time
ascending. It is a transparent, inspectable JSON of weighted dislikes by
attribute -- the web app's "Meh" control and chat-provided preferences both write
into the same file. Shape:

```json
{
  "version": 1,
  "updated_at": "2026-08-21T00:00:00Z",
  "source": {"Mommy Poppins SF": 1.0},
  "city": {"San Jose": 2.0},
  "area": {"South Bay": 1.5},
  "venue": {},
  "title_keyword": {"lego": 0.5},
  "drive_bucket": {"45-60": 1.0},
  "event_type": {"market": 1.0},
  "notes": ["too many far south-bay things (chat, 2026-08-21)"]
}
```

Each matching attribute adds `weight x 20` "virtual" drive-minutes to the event's
sort key. Each event in the output also carries `de_emphasis_penalty_minutes` and
`de_emphasis_reasons` so a de-emphasis is explainable.

## Conventions and gotchas

- **Keyless model path.** Extraction/curation use `scripts/claude_p.py`
  (`claude -p`). If an `ANTHROPIC_API_KEY` is later configured, switching to the
  cheaper litellm path is a change to those two call sites (see
  `use-ai-integration`).
- **Deep links.** `source_url` is always the event's own page where the source
  exposes one; when only a landing page is available it is used with
  `link_quality: "landing"` so the gap is visible.
- **Free services, be polite.** Nominatim is rate-limited to 1 req/s and cached;
  OSRM is the public demo server. Both are best-effort -- an event with no
  resolvable venue is kept as "location TBD".
- **Bounded growth.** The parse cache prunes entries older than 45 days, the
  geocache caps its entry count, and old run directories are pruned to the latest
  few -- so nothing accretes unbounded.
- Scripts: `scripts/run.py` (entry point + stages), `radar_types.py`,
  `radar_config.py`, `radar_dates.py`, `fetching.py`, `browser_fetch.py`
  (Fortress helper, run via the project venv), `parsing.py`, `extraction.py`,
  `locating.py`, `curating.py`, `assembling.py`, `caching.py`, `claude_p.py`.
