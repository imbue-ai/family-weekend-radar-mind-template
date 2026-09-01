"""A joyful weekend radar of fun, nearby kids' events for the family.

The index route renders the latest weekly snapshot produced by the
``kid-events-radar`` pipeline skill. The page only *reads* that snapshot -- it
never triggers a fetch on load; the pipeline runs on a weekly schedule and
overwrites the snapshot.

Services run from /home/user/workspace (the repo root). Conventions:

- Persistent state (anything written and read across runs -- cursors,
  caches, snapshots, user records): read and write it under ``DATA_DIR``
  (defined below), never a hardcoded ``data/.apps/kid-events-radar/`` at the
  call site. ``DATA_DIR`` defaults to ``data/.apps/kid-events-radar/`` but
  honors the ``KID_EVENTS_RADAR_DATA_DIR`` env var.
- Static assets shipped alongside this file: ``Path(__file__).parent / "assets/..."``.
- Listen port: bind ``PORT`` (defined below), overridable via ``KID_EVENTS_RADAR_PORT``.
"""

import datetime
import json
import os
from pathlib import Path

from flask import Flask, Response
from werkzeug.serving import run_simple

DATA_DIR = Path(os.environ.get("KID_EVENTS_RADAR_DATA_DIR", "data/.apps/kid-events-radar"))
PORT = int(os.environ.get("KID_EVENTS_RADAR_PORT", "8080"))

# The weekly snapshot written by the kid-events-radar pipeline skill. The page
# reads this file; it does not run the pipeline itself.
SNAPSHOT_PATH = Path(
    os.environ.get(
        "KID_EVENTS_RADAR_SNAPSHOT", "data/.skills/kid-events-radar/latest.json"
    )
)

app = Flask("kid_events_radar", static_folder=None)

_DAY_ABBR = {
    "Monday": "Mon", "Tuesday": "Tue", "Wednesday": "Wed", "Thursday": "Thu",
    "Friday": "Fri", "Saturday": "Sat", "Sunday": "Sun",
}


def _iso_week(datestr: str) -> tuple[int, int]:
    y, m, d = (int(x) for x in datestr.split("-"))
    iso = datetime.date(y, m, d).isocalendar()
    return (iso[0], iso[1])


def _map_event(e: dict) -> dict:
    """Map a pipeline event record to the shape the page renders."""
    drive = e.get("drive_minutes")
    return {
        "title": e.get("title", ""),
        "day": _DAY_ABBR.get(e.get("day", ""), e.get("day", "")),
        "time": e.get("start_time") or "",
        "venue": e.get("venue") or "",
        "city": e.get("city") or "",
        "drive": drive,  # may be null when the venue could not be located
        "fit": e.get("kid_fit") or e.get("raw_excerpt") or "",
        "raw": e.get("raw_excerpt") or "",
        "flag": e.get("flag") or "",
        "timeConfidence": e.get("time_confidence") or "high",
        "timeCandidates": e.get("time_candidates") or {},
        "source": e.get("source_name") or "",
        "url": e.get("source_url") or "",
        "linkQuality": e.get("link_quality") or "event",
        "penalty": e.get("de_emphasis_penalty_minutes") or 0,
    }


def _relative_label(idx: int, group: dict) -> str:
    if idx == 0:
        return "This weekend"
    if idx == 1:
        return "Next weekend"
    return group.get("label", "")


def build_payload() -> dict:
    """Read the latest snapshot and shape it for the page (no fetching)."""
    if not SNAPSHOT_PATH.exists():
        return {"weeks": [], "panel": [], "meta": {"missing": True}}

    d = json.loads(SNAPSHOT_PATH.read_text())
    weekends = d.get("weekends", [])
    evenings = d.get("thu_fri_evening", [])

    # Attach each Thu/Fri-evening event to its weekend (same ISO week); anything
    # that doesn't line up goes to the nearest weekend by date, so nothing drops.
    group_keys = [_iso_week(g["weekend_of"]) for g in weekends]
    buckets: list[list[dict]] = [[] for _ in weekends]
    for ev in evenings:
        date = ev.get("date")
        if not date or not weekends:
            continue
        key = _iso_week(date)
        if key in group_keys:
            buckets[group_keys.index(key)].append(ev)
        else:
            edate = datetime.date(*(int(x) for x in date.split("-")))
            nearest = min(
                range(len(weekends)),
                key=lambda gi: abs(
                    (datetime.date(*(int(x) for x in weekends[gi]["weekend_of"].split("-"))) - edate).days
                ),
            )
            buckets[nearest].append(ev)

    weeks = []
    for idx, g in enumerate(weekends):
        weeks.append({
            "label": _relative_label(idx, g),
            "range": g.get("label", ""),
            "weekend": [_map_event(e) for e in g.get("events", [])],
            "evenings": [_map_event(e) for e in buckets[idx]],
        })

    panel = [
        {"name": p.get("name", ""), "note": p.get("why") or p.get("note", ""), "url": p.get("url", "")}
        for p in d.get("check_yourself_panel", [])
    ]

    statuses = d.get("source_status", [])
    home = d.get("generated_for") or d.get("home_address", "")
    meta = {
        "missing": False,
        "fetchedAt": d.get("fetched_at", ""),
        "sourceTotal": len(statuses),
        "sourceWithEvents": sum(1 for s in statuses if s.get("event_count", 0) > 0),
        "homeShort": home.split(",")[0] if home else "",
        "ageFilter": d.get("age_filter", ""),
    }
    return {"weeks": weeks, "panel": panel, "meta": meta}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Weekend Radar</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fredoka:wght@400;500;600;700&family=Nunito:ital,wght@0,400;0,600;0,700;0,800;1,600&display=swap" rel="stylesheet">
<style>
  :root {
    --ink: #2b2438;
    --ink-soft: #6a6076;
    --paper: #fef6ee;
    --card: #ffffff;
    --poppy: #ff7a3d;
    --sky: #3da9e0;
    --meadow: #57c08a;
    --sunbeam: #ffc24b;
    --berry: #e85d9c;
    --line: #efe3d6;
    --shadow: 0 10px 24px -12px rgba(43,36,56,.28);
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--paper); color: var(--ink);
    font-family: "Nunito", system-ui, sans-serif;
    background-image:
      radial-gradient(circle at 12% -5%, rgba(255,194,75,.22), transparent 32%),
      radial-gradient(circle at 92% 0%, rgba(61,169,224,.16), transparent 34%);
    background-attachment: fixed;
  }
  .wrap { max-width: 940px; margin: 0 auto; padding: 28px 20px 64px; }

  header.hero { text-align: center; padding: 18px 0 6px; }
  .kicker {
    font-family: "Fredoka", sans-serif; font-weight: 600; letter-spacing: .12em;
    text-transform: uppercase; font-size: .8rem; color: var(--poppy);
  }
  h1 {
    font-family: "Fredoka", sans-serif; font-weight: 700;
    font-size: clamp(2.1rem, 6vw, 3.4rem); line-height: 1.02; margin: .12em 0 .18em;
  }
  .home-note { color: var(--ink-soft); font-weight: 600; font-size: 1rem; margin: 0; }
  .home-note b { color: var(--ink); }

  .pager {
    display: flex; align-items: center; justify-content: center; gap: 10px;
    margin: 26px auto 8px; flex-wrap: wrap;
  }
  .pager button {
    font-family: "Fredoka", sans-serif; font-weight: 600; font-size: .95rem;
    border: 2px solid var(--line); background: var(--card); color: var(--ink);
    border-radius: 999px; padding: 9px 16px; cursor: pointer; transition: .16s;
  }
  .pager button:hover:not(:disabled) { border-color: var(--poppy); transform: translateY(-1px); }
  .pager button:disabled { opacity: .38; cursor: default; }
  .pager .now {
    display:flex; flex-direction:column; align-items:center; min-width: 210px; padding: 4px 8px;
  }
  .pager .now .lbl { font-family:"Fredoka",sans-serif; font-weight:700; font-size:1.15rem; }
  .pager .now .rng { color: var(--ink-soft); font-weight:700; font-size:.85rem; }

  .section-head { display:flex; align-items:baseline; gap: 12px; margin: 30px 2px 12px; }
  .section-head h2 { font-family:"Fredoka",sans-serif; font-weight:600; font-size:1.5rem; margin:0; }
  .section-head .count { color: var(--ink-soft); font-weight:700; font-size:.95rem; }
  .evening .section-head h2 { color: var(--berry); }

  .grid { display:grid; grid-template-columns: repeat(auto-fill, minmax(270px,1fr)); gap: 14px; }

  .card {
    position: relative; background: var(--card); border: 1px solid var(--line);
    border-radius: 18px; padding: 16px 16px 14px; box-shadow: var(--shadow);
    display:flex; flex-direction:column; gap: 8px; transition: transform .16s, box-shadow .16s;
    animation: rise .5s both;
  }
  .card:hover { transform: translateY(-4px); box-shadow: 0 18px 30px -14px rgba(43,36,56,.34); }
  .card .toprow { display:flex; align-items:center; gap:10px; justify-content: space-between; }

  .pin {
    display:inline-flex; align-items:center; gap:6px; flex: 0 0 auto;
    font-family:"Fredoka",sans-serif; font-weight:700; font-size:.92rem;
    color:#fff; padding: 5px 11px; border-radius: 999px; white-space: nowrap;
  }
  .pin svg { width: 14px; height: 14px; }
  .daychip {
    font-family:"Fredoka",sans-serif; font-weight:600; font-size:.78rem;
    color: var(--ink-soft); background: #f7efe6; border-radius: 999px; padding: 4px 10px;
  }
  .verify {
    font-family:"Fredoka",sans-serif; font-weight:600; font-size:.72rem; cursor:help;
    color:#a15c10; background:#fff2d6; border:1px solid #f4d9a1; border-radius:999px; padding:3px 8px;
  }
  .card h3 { font-family:"Fredoka",sans-serif; font-weight:600; font-size:1.16rem; margin: 2px 0 0; line-height:1.15; }
  .flag {
    display:inline-flex; align-items:center; gap:5px; align-self:flex-start;
    font-family:"Fredoka",sans-serif; font-weight:600; font-size:.76rem;
    color:#a15c10; background:#fff2d6; border:1px solid #f4d9a1;
    border-radius:999px; padding:3px 9px 3px 7px; margin-top:1px;
  }
  .flag svg { width:13px; height:13px; }
  .venue { font-weight:800; color: var(--ink); font-size:.95rem; }
  .venue .city { color: var(--ink-soft); font-weight:700; }
  .fit { color: var(--ink-soft); font-size:.92rem; margin:0; line-height:1.35; flex:1; }
  .card .foot { display:flex; align-items:center; justify-content:space-between; margin-top:4px; }
  .src { font-size:.78rem; color: var(--ink-soft); font-weight:700; }
  .go {
    font-family:"Fredoka",sans-serif; font-weight:600; font-size:.86rem;
    color: var(--poppy); text-decoration:none; border-bottom: 2px solid transparent;
  }
  .go:hover { border-color: var(--poppy); }
  .actions { display:flex; align-items:center; gap:10px; }
  .meh {
    border:none; background:none; cursor:pointer; display:inline-flex; align-items:center; gap:5px;
    font-family:"Fredoka",sans-serif; font-weight:600; font-size:.82rem; color: var(--ink-soft);
    padding:4px 8px; border-radius:9px; transition:.14s;
  }
  .meh svg { width:15px; height:15px; }
  .meh:hover { color: var(--ink); background:#f3ece3; }
  .card.mehed { opacity:.5; }
  .card.mehed:hover { opacity:.78; }
  .toast {
    position: fixed; left:50%; bottom:24px; transform: translateX(-50%) translateY(8px);
    background: var(--ink); color:#fff; font-family:"Fredoka",sans-serif; font-weight:600; font-size:.92rem;
    padding:11px 18px; border-radius:999px; box-shadow: 0 12px 30px -10px rgba(43,36,56,.5);
    opacity:0; pointer-events:none; transition: opacity .2s, transform .2s; z-index:60;
  }
  .toast.show { opacity:1; transform: translateX(-50%) translateY(0); }

  .empty {
    text-align:center; padding: 40px 20px; color: var(--ink-soft);
    border: 2px dashed var(--line); border-radius: 18px; background: #fffaf3;
  }
  .empty .big { font-family:"Fredoka",sans-serif; font-size:1.2rem; color: var(--ink); margin-bottom:6px; }

  .panel {
    margin-top: 40px; background: #fffaf3; border:1px solid var(--line);
    border-radius: 18px; padding: 18px 20px;
  }
  .panel h2 { font-family:"Fredoka",sans-serif; font-weight:600; font-size:1.2rem; margin:0 0 4px; }
  .panel .sub { color: var(--ink-soft); font-weight:700; font-size:.9rem; margin:0 0 12px; }
  .panel ul { list-style:none; margin:0; padding:0; display:grid; gap:8px; }
  .panel li { display:flex; gap:10px; align-items:baseline; }
  .panel a { color: var(--sky); font-weight:800; text-decoration:none; }
  .panel a:hover { text-decoration:underline; }
  .panel .why { color: var(--ink-soft); font-weight:600; font-size:.88rem; }

  footer.foot { text-align:center; color: var(--ink-soft); font-weight:700; font-size:.82rem; margin-top:34px; }

  @keyframes rise { from { opacity:0; transform: translateY(10px); } to { opacity:1; transform:none; } }
  @media (prefers-reduced-motion: reduce) { .card { animation: none; } .card:hover{ transform:none; } }
</style>
</head>
<body>
<div class="wrap">
  <header class="hero">
    <div class="kicker">Family Weekend Radar</div>
    <h1>What's fun near us this weekend</h1>
    <p class="home-note" id="homenote"></p>
  </header>

  <nav class="pager">
    <button id="prev">‹ Back</button>
    <div class="now"><span class="lbl" id="wlabel"></span><span class="rng" id="wrange"></span></div>
    <button id="next">Forward ›</button>
  </nav>

  <section id="weekend">
    <div class="section-head"><h2>Weekend</h2><span class="count" id="wkcount"></span></div>
    <div class="grid" id="wkgrid"></div>
  </section>

  <section class="evening" id="evening">
    <div class="section-head"><h2>Thursday &amp; Friday, early evening</h2><span class="count" id="evcount"></span></div>
    <div class="grid" id="evgrid"></div>
  </section>

  <aside class="panel">
    <h2>Worth a peek yourself</h2>
    <p class="sub">These can't be pulled in automatically — but they're where the real insider tips live.</p>
    <ul id="panel"></ul>
  </aside>

  <footer class="foot" id="foot"></footer>
</div>
<div class="toast" id="toast"></div>

<script>
  const DATA = __DATA__;
  const WEEKS = DATA.weeks;
  const PANEL = DATA.panel;
  const META = DATA.meta;
  let i = 0;
  const MEHED = new Set();
  const MEH_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><line x1="8.5" y1="15" x2="15.5" y2="15"/><line x1="9" y1="9.5" x2="9.01" y2="9.5"/><line x1="15" y1="9.5" x2="15.01" y2="9.5"/></svg>';
  const PIN_SVG = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s-6-5.3-6-10a6 6 0 0 1 12 0c0 4.7-6 10-6 10z"/><circle cx="12" cy="11" r="2"/></svg>';

  function pinColor(min){
    const cs = getComputedStyle(document.documentElement);
    if (min == null) return '#b7ab9c';
    if (min <= 15) return cs.getPropertyValue('--meadow');
    if (min <= 30) return cs.getPropertyValue('--sky');
    if (min <= 45) return cs.getPropertyValue('--sunbeam');
    return cs.getPropertyValue('--poppy');
  }
  function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

  function card(ev){
    const hasDrive = ev.drive != null;
    const color = pinColor(ev.drive).trim();
    const pinTxt = hasDrive ? ev.drive + ' min' : 'location TBD';
    const mehed = MEHED.has(ev.title);
    const cands = Object.values(ev.timeCandidates || {});
    const verify = ev.timeConfidence === 'low'
      ? `<span class="verify" title="Sources disagree on the time${cands.length ? ' (' + cands.map(esc).join(' vs ') + ')' : ''} — double-check before you go">verify time</span>`
      : '';
    return `<article class="card${mehed ? ' mehed' : ''}" data-title="${esc(ev.title)}">
      <div class="toprow">
        <span class="pin" style="background:${color}">${PIN_SVG}${pinTxt}</span>
        <span class="daychip">${esc(ev.day)}${ev.time ? ' · ' + esc(ev.time) : ''}</span>
      </div>
      <h3>${esc(ev.title)}</h3>
      ${verify}
      ${ev.flag ? `<span class="flag"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 9a3 3 0 0 1 0 6v2a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-2a3 3 0 0 1 0-6V7a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2z"/><path d="M13 5v14"/></svg>${esc(ev.flag)}</span>` : ''}
      <div class="venue">${esc(ev.venue)} <span class="city">· ${esc(ev.city)}</span></div>
      <p class="fit" title="${esc(ev.raw)}">${esc(ev.fit)}</p>
      <div class="foot">
        <span class="src">via ${esc(ev.source)}</span>
        <div class="actions">
          <button class="meh" type="button" title="Show fewer like this">${MEH_SVG}${mehed ? 'Undo' : 'Meh'}</button>
          <a class="go" href="${esc(ev.url)}" target="_blank" rel="noopener">See details ›</a>
        </div>
      </div>
    </article>`;
  }

  // Effective distance = drive time + any de-emphasis penalty; unlocated + mehed sink to the bottom.
  function eff(ev){ return (ev.drive == null ? 9999 : ev.drive) + (ev.penalty || 0) + (MEHED.has(ev.title) ? 100000 : 0); }
  function ordered(list){ return [...list].sort((a,b) => eff(a) - eff(b)); }

  function render(){
    const w = WEEKS[i];
    document.getElementById('wlabel').textContent = w.label;
    document.getElementById('wrange').textContent = w.range;
    document.getElementById('prev').disabled = (i === 0);
    document.getElementById('next').disabled = (i === WEEKS.length - 1);

    const wk = document.getElementById('wkgrid');
    document.getElementById('wkcount').textContent = w.weekend.length ? w.weekend.length + ' to choose from' : '';
    wk.innerHTML = w.weekend.length
      ? ordered(w.weekend).map(card).join('')
      : `<div class="empty" style="grid-column:1/-1"><div class="big">Nothing here yet for this weekend</div>Events show up as the sources post them. Check back after Monday's refresh.</div>`;

    const ev = document.getElementById('evgrid');
    document.getElementById('evcount').textContent = w.evenings.length ? w.evenings.length + ' options' : '';
    ev.innerHTML = w.evenings.length
      ? ordered(w.evenings).map(card).join('')
      : `<div class="empty" style="grid-column:1/-1">No early-evening picks yet for this week.</div>`;

    // Cap the stagger so a big weekend (28+ cards) still appears fast.
    document.querySelectorAll('.card').forEach((c, n) => { c.style.animationDelay = (Math.min(n, 10) * 30) + 'ms'; });
  }

  document.getElementById('prev').onclick = () => { if (i>0){ i--; render(); } };
  document.getElementById('next').onclick = () => { if (i<WEEKS.length-1){ i++; render(); } };

  function showToast(msg){
    const t = document.getElementById('toast');
    t.textContent = msg; t.classList.add('show');
    clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600);
  }
  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.meh'); if (!btn) return;
    const title = btn.closest('.card').getAttribute('data-title');
    if (MEHED.has(title)) { MEHED.delete(title); showToast('Back in the mix.'); }
    else { MEHED.add(title); showToast('Got it — I\\'ll show fewer like this.'); }
    render();
  });

  // Empty state when no snapshot exists yet.
  if (META.missing || !WEEKS.length) {
    document.getElementById('homenote').textContent = '';
    document.querySelector('.pager').style.display = 'none';
    document.getElementById('weekend').innerHTML = '<div class="empty"><div class="big">The first weekly pull hasn\\'t run yet</div>Once it does, this weekend\\'s events will appear here.</div>';
    document.getElementById('evening').style.display = 'none';
  } else {
    document.getElementById('homenote').innerHTML =
      'Sorted by drive from <b>' + esc(META.homeShort) + '</b> · ages ' + esc(META.ageFilter) + ' · within an hour';
    document.getElementById('panel').innerHTML = PANEL.map(p =>
      `<li><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.name)}</a><span class="why">${esc(p.note)}</span></li>`
    ).join('');
    let when = '';
    if (META.fetchedAt) {
      const dt = new Date(META.fetchedAt);
      if (!isNaN(dt)) when = dt.toLocaleDateString(undefined, {month:'short', day:'numeric'});
    }
    document.getElementById('foot').textContent =
      (when ? 'Updated ' + when + '. ' : '') + 'Refreshes every Monday · pulled from ' +
      META.sourceWithEvents + ' of ' + META.sourceTotal + ' sources.';
    render();
  }
</script>
</body>
</html>
"""


@app.route("/")
def index() -> Response:
    payload = build_payload()
    html = PAGE.replace("__DATA__", json.dumps(payload))
    return Response(html, mimetype="text/html")


@app.route("/health")
def health() -> Response:
    return Response('{"status": "ok"}', mimetype="application/json")


def main() -> None:
    run_simple(
        "127.0.0.1", PORT, app, threaded=True, use_reloader=False, use_debugger=False
    )


if __name__ == "__main__":
    main()
