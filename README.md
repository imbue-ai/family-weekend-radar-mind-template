<p align="center">
  <img alt="Family Weekend Radar" src="template.svg" width="480">
</p>

# Family Weekend Radar

<p align="center">
  <a href="https://boweiliu.github.io/open-in-minds/?git_url=https://github.com/matthewboulos/inspiration-weekend"><img alt="Open in Minds" height="64" src="https://img.shields.io/badge/Open%20in%20Minds-D8D1C0?style=for-the-badge"></a>
</p>

Didn't work? Create a Minds workspace and paste this to your agent:
` /use-template https://github.com/matthewboulos/inspiration-weekend`

## Why you care

A weekly, self-updating radar of fun, kid-appropriate local events near home -- weekend-first with drive times, source links, flags for ticketed events, and a guided setup to point it at your own city.

The best local kids' events -- the free museum day, the fire-truck open house,
the neighborhood festival -- travel by word of mouth through a network of other
parents, and if you don't have that network you just miss them. Family Weekend
Radar is that network in software: every week it quietly rounds up the fun,
age-appropriate events happening near your home, sorts them by how far you'd
have to drive, and lays them out weekend by weekend so you can plan ahead
instead of finding out too late. It works out of the box for the San Francisco
Bay Area and adapts to any city through a guided setup.

## How to use it

Family Weekend Radar is a single tab you open in your Minds workspace. It has
two halves that meet at one weekly snapshot file: a pipeline that gathers the
events, and a web page that shows them.

**The page.** It opens on *this weekend* as a wall of colorful event cards. Each
card shows:

- a **drive-time pin** -- a map-pin badge with the minutes from your home,
  colored green when it's close and warm-orange when it's a haul (and "location
  TBD" when the venue couldn't be pinned);
- the **event**, its venue and city, the day and time, and a one-line "why a kid
  would like this";
- **caveat badges** where they apply -- ticketed, fundraiser, registration
  required, or a subtle "verify time" hint when two sources disagree on the
  start time;
- a **"See details"** deep link straight to the event's own page.

A pager at the top steps forward through the next several weekends, so you can
plan ahead. A **Thursday/Friday early-evening** section catches the after-work,
before-bedtime options. A **"Meh"** button on any card de-emphasizes things you
aren't interested in, and a **"worth a peek yourself"** panel lists the
human-only sources (parent Facebook groups, Instagram accounts) that can't be
automated. Until the first weekly pull has run, the page shows a friendly empty
state rather than an error.

**The weekly pull.** The page never fetches on load -- it just renders the latest
snapshot. That snapshot is produced by the pipeline, which you run on a schedule
(a Monday-morning job works well) or by hand:

```
uv run .agents/skills/kid-events-radar/scripts/run.py run all
```

That one command fetches every source, filters by age and drive radius, geocodes
venues and computes drive times, curates out the adult / non-kid events,
de-dupes, and overwrites the snapshot the page reads. To add or vet a new source
before trusting it, there's a `verify-source` subcommand that tests a candidate
URL and tells you whether it's usable.

## Ideas for making it yours

Once it's yours, here are a few directions to take it:

- **Widen the age band as your kids grow.** Bump `--age-min` / `--age-max` (or
  the config defaults) and the curation naturally starts keeping tween- and
  teen-friendly events too.
- **Add a source you love.** Found a great local parent blog or a museum's event
  feed? Run it through `verify-source`, and if it passes, drop the printed
  `[[source]]` block into `assets/sources.toml` -- no code change.
- **Change the rhythm.** The pipeline is just a scheduled command, so you can run
  it more or less often -- a Friday-afternoon refresh, say, so the weekend view
  is always fresh.
- **Tune what "close" means.** The pin colors bucket drive time (green / blue /
  amber / orange); shift those thresholds in the app to match how far you'll
  actually travel for a good afternoon.
- **Make the "Meh" preferences stick.** Today the de-emphasize control lives in
  the page for the session; wire it back to the pipeline's preference profile so
  your dislikes carry across every weekly run.

## What this is

This repository is a published **minds template**: a clean, bootable
snapshot of what a mind built, ready to adapt into your own. It is NOT the
generic workspace template -- it is this specific project.

[`template.md`](template.md) is the full manifest -- what it is, how it
works, what it needs to run, and what to adapt -- with the
machine-readable half (recipe, requirements, and the environment it needs
installed) in [`template.toml`](template.toml).
