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

<!-- FILL-IN (publishing agent): BEFORE reporting done, replace this comment
with a one-paragraph overview of what this template does for its user: the
problem it solves, the main things it produces (pages, reports, automations),
and what the user sees when it is running. Write for a reader who has never
seen the original mind. -->

## How it works

The snapshot includes these paths (each is a repo-root-relative path copied
from the original mind onto a clean default-workspace-template base):

- `system/apps/kid_events_radar`
- `.agents/skills/kid-events-radar`
- `system/supervisord.conf`
- `pyproject.toml`

<!-- FILL-IN (publishing agent): BEFORE reporting done, replace this comment
with prose that makes the list above self-explanatory: for each included path,
say what it is (an app or lib with code, a skill, data) and what role it plays.
Then describe how the pieces wire together at runtime: which supervisord
programs (in system/supervisord.conf) run them, which ports they listen on and how
those are registered in forward_port.py (if applicable), and any scripts or
services that connect them. -->

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

<!-- FILL-IN (publishing agent): BEFORE reporting done, replace this comment
with both kinds of entry.

ACTIVATION -- one line each, using exactly these forms (greppable by `requires_`):

- requires_permission: <latchkey scope> / <permission schema> (user-approved;
  the adopting agent initiates this via a latchkey permission request during
  setup -- it must not merely mention it)
- requires_secret: <ENV_VAR or config key> (what it is for and where to put it)
- requires_llm: <how the code reaches Claude, and what an adopter needs>
  (include this line whenever the app calls an LLM: name the method it was
  built for -- keyed litellm via ANTHROPIC_API_KEY, or keyless subscription via
  claude -p -- so an adopter on the other method knows to switch it per the
  use-ai-integration skill)

Derive the real values from the included code (e.g. every service the app
calls through `latchkey curl`, and whether any code calls an LLM). Example:
- requires_permission: slack-api / slack-read-all (user-approved; adopting
  agent initiates during setup)
- requires_llm: calls Claude via the keyed litellm path (ANTHROPIC_API_KEY set);
  an adopter on the keyless subscription path must switch the model calls per
  use-ai-integration

These lines are what the ADOPTING agent acts on during setup, so a vague or
missing one silently breaks adoption -- a real incident: an adopter was never
prompted for a Slack permission the app needed. They are also what the lead
surfaces back to the publishing user for confirmation, so the list must be
complete and accurate. EVERY line must have its counterpart in
`template.toml`'s `[requirements]` (`[[requirements.permission]]`,
`[[requirements.secret]]`, `[requirements.llm]`); the validator compares
them and fails the publish if they disagree.

ADAPTATION -- one bullet each, in plain prose: every gap the adapter must
decide or rewire (stubbed integrations, hardcoded accounts/channels/ids, data
that was not included, anything that will not work out of the box). For each,
say what is missing and what a working replacement looks like. Mirror them as
`[[requirements.adaptation]]` entries in the TOML.

Do not repeat the README's "Ideas for making it yours" here -- those are
optional invitations, these are things that must be resolved.

If there is genuinely nothing of either kind, write exactly: "No requirements --
runs as published, with no external permissions or secrets." -->

## Environment

What this template needs INSTALLED, beyond what the template already has.
Declared in `template.toml`'s `[environment]` table; an adopting mind
converges it at ITS OWN pinned apt snapshot timestamp, so package versions come
out consistent with the rest of that mind's environment rather than frozen to
whatever this publisher happened to have.

<!-- FILL-IN (publishing agent): BEFORE reporting done, replace this comment
with a plain-language summary of what gets installed and why -- one line per
thing, naming what needs it (e.g. "poppler-utils: the digest renders PDF
attachments to text"). Fill in the matching entries in template.toml's
[environment] table at the same time; that table is what actually installs
anything, and this prose is what a human reads.

Derive it from the included code, not from what happens to be installed on this
machine: every binary the code shells out to, every global npm/uv/cargo tool it
invokes. If it needs nothing beyond the template's own environment, write
exactly: "Nothing extra -- runs on the stock workspace environment." -->

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

<!-- FILL-IN (publishing agent): BEFORE reporting done, replace this comment with
the first entry, in the form:
### v1 (YYYY-MM-DD) -- <one line: what this first version publishes>
using today's date. A later update of this template (the update-published-template
flow) appends "### v2 (date) -- what changed since v1", and so on. -->

## Adaptation history

Each mind that adapts this template appends one dated entry below. Earlier
entries are never rewritten.
