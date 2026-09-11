<p align="center">
  <img alt="Family Weekend Radar" src="template.svg" width="480">
</p>

# Family Weekend Radar

<p align="center">
  <a href="https://boweiliu.github.io/open-in-minds/?git_url=https://github.com/imbue-ai/family-weekend-radar-mind-template"><img alt="Open in Minds" height="64" src="https://img.shields.io/badge/Open%20in%20Minds-D8D1C0?style=for-the-badge"></a>
</p>

Didn't work? Create a Minds workspace and paste this to your agent:
` /use-template https://github.com/imbue-ai/family-weekend-radar-mind-template`

## Why you care

A weekly, self-updating radar of fun, kid-appropriate local events near home -- weekend-first with drive times, source links, flags for ticketed events, and a guided setup to point it at your own city.

The best local kids' events -- a fire-truck open house, a free museum day, a
neighborhood festival -- spread by word of mouth through a network of other
parents, and if you don't have that network you simply miss them. This
template stands in for that network: once a week it pulls a dozen family-event
sources, keeps only what fits your kid's age and a drivable radius of home,
works out the drive time, and hands you a clean weekend-by-weekend list instead
of a dozen tabs to check yourself.

## How to use it

Once adopted, a "Family Weekend Radar" tab opens on this weekend as a wall of
event cards -- each with a colored map-pin badge for the drive time, the venue
and city, a one-line "why a kid would like this," caveat badges (ticketed,
fundraiser, registration required, unverified time), and a link to the event's
own page. A pager steps forward through the next several weekends, a "Meh"
button de-emphasizes things you're not interested in, and a "worth a peek
yourself" panel lists the human-only sources (parent Facebook groups,
Instagram accounts) that can't be automated.

The page only ever reads a snapshot file -- it never fetches on load. The
snapshot comes from the `kid-events-radar` skill's `run all` command, which you
(or a scheduled job) run weekly: it fetches the sources, filters and geocodes
events, computes drive times, de-dupes, and writes the new snapshot. Until the
first run, the tab shows a friendly "hasn't run yet" empty state rather than an
error.

## Ideas for making it yours

- Swap the shipped SF Bay Area source list for your own metro's family-event
  sites, parent blogs, and library/museum calendars -- the skill's
  `verify-source` subcommand lets you test a candidate URL before adding it.
- Wire the "Meh" button to persist across weeks instead of resetting each
  session, so the radar quietly learns what your family skips.
- Add a second age band (e.g. a teen sibling) and show both age groups' event
  sets side by side.
- Feed the weekly snapshot into a Sunday-night digest message instead of (or
  alongside) the web tab.
- Extend the caveat flags with your own categories -- "outdoors," "free
  parking," "stroller-friendly" -- if those are what actually decide whether
  your family goes.

## What this is

This repository is a published **minds template**: a clean, bootable
snapshot of what a mind built, ready to adapt into your own. It is NOT the
generic workspace template -- it is this specific project.

[`template.md`](template.md) is the full manifest -- what it is, how it
works, what it needs to run, and what to adapt -- with the
machine-readable half (recipe, requirements, and the environment it needs
installed) in [`template.toml`](template.toml).
