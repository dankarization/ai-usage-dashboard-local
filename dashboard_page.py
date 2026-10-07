"""Self-contained local dashboard without external assets.

Layout and design tokens are adapted from the OpenCodex GUI design system
(https://github.com/lidge-jun/opencodex, MIT License, (c) 2026 opencodex
contributors): role-based colour tokens, a 10/11/12/13/14/16/20/24 type scale,
a 4px spacing base, 4/6/8/12/16/pill radii, and the rule that a container is
added to group information, not merely to divide it. No OpenCodex source code
or assets are copied; only published token values and layout conventions.

The page is a single HTML document served by ``local_display_service``. It keeps
these invariants:

* No external assets: no ``<script src=``, no ``<link>``, no ``innerHTML``.
* No invented data: every number comes from a live endpoint, and a window with
  no percentage renders as "unavailable"/"not configured", never as 0%.
* Honest history: the daily chart uses Gateway model-day and direct NordRouter
  account-day buckets; resizing reuses the same snapshot.
* Honest snapshot time: the header renders the offset-aware ``generated_at_utc``
  in the viewer's own timezone with a relative age, and marks a stale snapshot
  instead of presenting it as current.
"""

DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>AI Usage Dashboard</title>
<style>
/* Design tokens adapted from the OpenCodex design system (MIT). */
:root{
  --bg:#1c1c1c; --surface:#232323; --raised:#2b2b2b; --line:#343434; --line-soft:#2c2c2c;
  --text:#ececec; --muted:#a6a6a6; --faint:#8a8a8a;
  --green:#4ecb9d; --amber:#fbbf24; --red:#f87171; --accent:#7aa2f7; --neutral:#8a8a8a;
  --radius-xs:6px; --radius-sm:8px; --radius:12px; --pill:999px;
  --s1:4px; --s2:8px; --s3:12px; --s4:16px; --s5:20px; --s6:24px;
  --font-ui:-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --font-code:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --motion-fast:120ms;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:var(--bg);color:var(--text);
  font:13px/1.35 var(--font-ui);
  -webkit-font-smoothing:antialiased;
  padding:0 0 36px;
}
.wrap{max-width:1120px;margin:0 auto;padding:0 var(--s5)}

/* ---------- top bar ---------- */
.topbar{position:sticky;top:0;z-index:20;background:var(--bg);border-bottom:1px solid var(--line)}
.topbar-in{max-width:1120px;margin:0 auto;padding:9px var(--s5);display:flex;align-items:center;gap:var(--s3)}
.brand{display:flex;align-items:center;gap:var(--s2);min-width:0}
.dot{width:7px;height:7px;border-radius:var(--pill);background:var(--green);flex:none}
h1{font-size:14px;margin:0;font-weight:600;white-space:nowrap;letter-spacing:-.01em}
.spacer{flex:1 1 auto}
.stamp{color:var(--faint);font-size:11px;font-variant-numeric:tabular-nums;white-space:nowrap}
.stamp.stale{color:var(--amber)}
.btn{
  appearance:none;border:1px solid var(--line);background:var(--raised);color:var(--text);
  font:500 13px/1 var(--font-ui);padding:7px 13px;border-radius:var(--radius-sm);cursor:pointer;
  display:inline-flex;align-items:center;gap:6px;transition:background var(--motion-fast),border-color var(--motion-fast);
}
.btn:hover{background:#333;border-color:#444}
.btn[disabled]{opacity:.55;cursor:progress}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#14171f}
.btn.primary:hover{background:#8fb3f9;border-color:#8fb3f9}
.period{display:flex;gap:4px;flex:none}
.period .btn[aria-pressed="true"]{background:var(--accent);border-color:var(--accent);color:#14171f}
.pulse{width:6px;height:6px;border-radius:var(--pill);background:currentColor}
.busy .pulse{animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:.25}50%{opacity:1}}

/* ---------- sections: flat, hairline separated ---------- */
section{margin-top:var(--s5)}
.sec-head{display:flex;align-items:baseline;gap:var(--s2);flex-wrap:wrap;margin:0 0 var(--s2)}
h2{font-size:12px;margin:0;font-weight:600;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}
.note{color:var(--faint);font-size:11px;margin:0}
h3{font-size:12px;margin:0 0 var(--s2);font-weight:600;color:var(--muted)}
.panel{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:var(--s3) var(--s4)}
.panel.flat{padding:2px var(--s4)}

/* ---------- summary strip ---------- */
.strip{display:flex;flex-wrap:wrap;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden}
.strip-item{padding:11px var(--s4);border-left:1px solid var(--line);min-width:0;flex:1 1 150px}
.strip-item:first-child{border-left:0}
.strip-item .k{color:var(--faint);font-size:11px;margin-bottom:3px;white-space:nowrap}
.strip-item .v{font-size:20px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums;line-height:1.2}
.strip-item .v.unknown{color:var(--faint);font-weight:500}
.strip-item .s{color:var(--faint);font-size:11px;margin-top:2px;overflow-wrap:anywhere}
.pillrow{display:flex;gap:6px;flex-wrap:wrap;padding:9px var(--s4);border-top:1px solid var(--line);width:100%}
.pill{font-size:11px;color:var(--muted);border:1px solid var(--line);background:var(--raised);border-radius:var(--pill);padding:2px 8px;font-variant-numeric:tabular-nums;max-width:100%;overflow-wrap:anywhere}

/* ---------- unified limit rows ---------- */
.limit-group{padding:10px 0;border-top:1px solid var(--line-soft)}
.limit-group:first-child{border-top:0}
.limit-group.subdued{opacity:.7}
.limit-head{display:flex;align-items:center;gap:var(--s2);flex-wrap:wrap;margin-bottom:6px}
.brandtile{
  display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;
  border-radius:var(--radius-xs);border:1px solid var(--line);background:var(--raised);
  font-weight:700;font-size:9px;flex:none;letter-spacing:-.02em;color:var(--muted)
}
.pname{font-size:13px;font-weight:600}
.acct{font-family:var(--font-code);font-size:11px;color:var(--faint)}
.chip{margin-left:auto;font-size:11px;font-weight:500;padding:2px 8px;border-radius:var(--pill);border:1px solid var(--line);color:var(--muted);background:var(--raised);white-space:nowrap}
.chip.ok{color:var(--green);border-color:#2f5c4b;background:#1d2b26}
.chip.warn,.chip.stale{color:var(--amber);border-color:#5a4a1c;background:#2a2416}
.chip.danger{color:var(--red);border-color:#5c3030;background:#2b1d1d}
.chip.unknown,.chip.not_configured,.chip.unavailable{color:var(--neutral);border-color:var(--line);background:var(--raised)}

.win{display:grid;grid-template-columns:160px minmax(0,1fr) auto;gap:var(--s3);align-items:center;padding:3px 0}
.win-label{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.track{height:4px;border-radius:var(--pill);background:#171717;border:1px solid var(--line-soft);overflow:hidden}
.fill{height:100%;border-radius:var(--pill);transition:width 400ms ease}
.fill.ok{background:var(--green)} .fill.warn{background:var(--amber)} .fill.danger{background:var(--red)}
.fill.unknown{background:repeating-linear-gradient(135deg,#3a3a3a 0 4px,#2c2c2c 4px 8px)}
.win-val{font-size:12px;font-variant-numeric:tabular-nums;color:var(--text);white-space:nowrap}
.win-val .rem{color:var(--faint);font-size:11px}
.win-reset{grid-column:2/4;font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}
.unavailable{margin:2px 0 0;color:var(--faint);font-size:11px;font-style:italic}

/* ---------- nordrouter ---------- */
.nr-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));border-top:1px solid var(--line)}
.nr-cell{padding:9px var(--s4) 9px 0}
.nr-cell .k{color:var(--faint);font-size:11px;margin-bottom:2px}
.nr-cell .v{font-size:16px;font-weight:600;font-variant-numeric:tabular-nums}
.nr-cell .v.unknown{color:var(--faint);font-weight:500}
.two-col{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:var(--s5);margin-top:var(--s3)}
ol.top{margin:0;padding:0;list-style:none}
ol.top li{display:flex;justify-content:space-between;gap:var(--s3);padding:4px 0;border-top:1px solid var(--line-soft);font-size:12px}
ol.top li:first-child{border-top:0}
ol.top .id{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--font-code);font-size:11px}
ol.top .amt{color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap;font-size:11px}

/* ---------- chart ---------- */
.chart-wrap{width:100%;overflow:hidden}
svg.chart{display:block;width:100%;height:150px}
.legend{display:flex;gap:var(--s4);flex-wrap:wrap;color:var(--faint);font-size:11px;padding:6px 0 0}
.legend span{display:inline-flex;align-items:center;gap:5px}
.swatch{width:8px;height:8px;border-radius:2px;display:inline-block;background:var(--accent)}

/* ---------- table ---------- */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:6px var(--s3) 6px 0;border-bottom:1px solid var(--line-soft);overflow-wrap:anywhere;vertical-align:middle}
th{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.04em;font-weight:500;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.src{font-family:var(--font-code);font-size:11px;color:var(--faint)}
.sort-btn{appearance:none;border:0;background:none;color:inherit;font:inherit;text-transform:inherit;letter-spacing:inherit;padding:2px 0;cursor:pointer;white-space:nowrap}
.sort-btn:hover{color:var(--text)}
.sort-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
.sort-mark{display:inline-block;min-width:12px;margin-left:3px;color:var(--accent)}
.bar-mini{height:3px;border-radius:var(--pill);background:#171717;overflow:hidden;margin-top:4px;max-width:180px;border:1px solid var(--line-soft)}
.bar-mini i{display:block;height:100%;background:var(--accent)}

/* ---------- misc ---------- */
#error{color:#f0a8a2;font-size:12px;margin:var(--s3) 0 0}
.empty{color:var(--faint);font-size:12px;font-style:italic;margin:6px 0}
.foot{margin-top:var(--s6);color:var(--faint);font-size:11px;text-align:center}

@media (max-width:640px){
  .wrap{padding:0 var(--s3)}
  .topbar-in{padding:8px var(--s3);gap:var(--s2);flex-wrap:wrap}
  .brand{flex:1 1 auto}
  .spacer{display:none}
  .stamp{order:3;flex:1 0 100%;white-space:normal}
  h1{font-size:13px}
  .stamp{font-size:10px}
  .strip-item{flex:1 1 44%;padding:9px var(--s3);border-top:1px solid var(--line)}
  .strip-item:nth-child(-n+2){border-top:0}
  .strip-item:nth-child(odd){border-left:0}
  .strip-item .k{white-space:normal}
  .strip-item .v{font-size:16px}
  .panel{padding:var(--s2) var(--s3)}
  .panel.flat{padding:2px var(--s3)}
  .win{grid-template-columns:1fr;gap:2px;padding:5px 0}
  .win-reset{grid-column:1}
  .win-val{order:2}
  svg.chart{height:130px}
  .btn{padding:8px 12px}
}
@media (prefers-reduced-motion:reduce){
  *{animation:none!important;transition:none!important}
}
</style>
</head>
<body>
<header class="topbar">
  <div class="topbar-in">
    <div class="brand">
      <span class="dot" aria-hidden="true"></span>
      <h1>AI Usage Dashboard</h1>
    </div>
    <div class="spacer"></div>
    <span id="stamp" class="stamp" aria-live="polite">Loading…</span>
    <div class="period" role="group" aria-label="Usage period">
      <button id="period-30" class="btn" type="button" aria-pressed="true">30d</button>
      <button id="period-7" class="btn" type="button" aria-pressed="false">7d</button>
    </div>
    <button id="refresh" class="btn primary" type="button"><span class="pulse" aria-hidden="true"></span>Refresh</button>
  </div>
</header>

<main class="wrap">
  <p id="error" role="status" aria-live="polite"></p>

  <section aria-labelledby="h-hero">
    <div class="sec-head"><h2 id="h-hero">Overview</h2><p class="note" id="overview-note">Canonical direct NordRouter + non-NordRouter OpenClaw</p></div>
    <div id="hero" class="strip"></div>
  </section>

  <section aria-labelledby="h-codex">
    <div class="sec-head"><h2 id="h-codex">Codex accounts</h2></div>
    <div class="panel flat"><div id="codex"></div></div>
  </section>

  <section aria-labelledby="h-grok">
    <div class="sec-head"><h2 id="h-grok">Grok</h2></div>
    <div class="panel flat"><div id="quotas"></div></div>
  </section>

  <section aria-labelledby="h-nord">
    <div class="sec-head"><h2 id="h-nord">NordRouter</h2></div>
    <div class="panel">
      <div id="metrics" class="nr-grid"></div>
      <div class="two-col">
        <div><h3>Top-5 models today</h3><div id="today"></div></div>
        <div><h3 id="selected-top-label">Top-5 models · 30d</h3><div id="week"></div></div>
      </div>
    </div>
  </section>

  <section aria-labelledby="h-history">
    <div class="sec-head"><h2 id="h-history">Usage history</h2></div>
    <div id="history" class="panel chart-wrap"></div>
  </section>

  <section aria-labelledby="h-costs">
    <div class="sec-head"><h2 id="h-costs">NordRouter model costs</h2><p class="note" id="costs-note">Direct billed model totals; model rows may differ from account window total</p></div>
    <div class="panel"><div class="scroll"><table>
      <thead><tr><th>Model</th><th class="num">Tokens</th><th class="num">Billed USD</th></tr></thead>
      <tbody id="cost-models"></tbody>
    </table></div></div>
  </section>

  <section aria-labelledby="h-models">
    <div class="sec-head"><h2 id="h-models">OpenClaw model usage</h2><p class="note" id="models-note">NordRouter-route rows excluded from Overview and history totals to avoid double-counting</p></div>
    <div class="panel">
      <p id="model-stamp" class="note"></p>
      <div class="scroll">
        <table id="model-table">
          <thead><tr>
            <th id="model-sort-head-source"><button id="model-sort-source" class="sort-btn" type="button">Route<span id="model-sort-mark-source" class="sort-mark" aria-hidden="true">↕</span></button></th>
            <th id="model-sort-head-model"><button id="model-sort-model" class="sort-btn" type="button">Model / day<span id="model-sort-mark-model" class="sort-mark" aria-hidden="true">↕</span></button></th>
            <th id="model-sort-head-input" class="num"><button id="model-sort-input" class="sort-btn" type="button">Input<span id="model-sort-mark-input" class="sort-mark" aria-hidden="true">↕</span></button></th>
            <th id="model-sort-head-output" class="num"><button id="model-sort-output" class="sort-btn" type="button">Output<span id="model-sort-mark-output" class="sort-mark" aria-hidden="true">↕</span></button></th>
            <th id="model-sort-head-cache_read" class="num"><button id="model-sort-cache_read" class="sort-btn" type="button">Cache read<span id="model-sort-mark-cache_read" class="sort-mark" aria-hidden="true">↕</span></button></th>
            <th id="model-sort-head-cache_write" class="num"><button id="model-sort-cache_write" class="sort-btn" type="button">Cache write<span id="model-sort-mark-cache_write" class="sort-mark" aria-hidden="true">↕</span></button></th>
            <th id="model-sort-head-total" class="num" aria-sort="descending"><button id="model-sort-total" class="sort-btn" type="button">Total<span id="model-sort-mark-total" class="sort-mark" aria-hidden="true">↓</span></button></th>
            <th id="model-sort-head-usd" class="num"><button id="model-sort-usd" class="sort-btn" type="button">USD<span id="model-sort-mark-usd" class="sort-mark" aria-hidden="true">↕</span></button></th>
          </tr></thead>
          <tbody id="models"></tbody>
        </table>
      </div>
    </div>
  </section>

  <p class="foot">Auto-refresh every 5m · data never leaves this machine</p>
</main>

<script>
'use strict';
var $ = function (id) { return document.getElementById(id); };
var number = function (value) { return typeof value === 'number' && Number.isFinite(value); };

function usd(value) {
  if (!number(value)) return '—';
  var digits = Math.abs(value) >= 1 ? 2 : 4;
  return '$' + value.toFixed(digits);
}
function tokens(value) {
  if (!number(value)) return '—';
  if (value >= 1e9) return (value / 1e9).toFixed(2) + 'B';
  if (value >= 1e6) return (value / 1e6).toFixed(1) + 'M';
  if (value >= 1e3) return (value / 1e3).toFixed(1) + 'K';
  return String(value);
}
function node(tag, text, parent) {
  var element = document.createElement(tag);
  if (text !== undefined && text !== null) element.textContent = text;
  if (parent) parent.append(element);
  return element;
}
function two(value) { return String(value).padStart(2, '0'); }
/* ISO daily buckets are calendar dates, not instants: never timezone-shift them. */
function dateOnly(value) {
  var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || ''));
  return match ? match[3] + '.' + match[2] + '.' + match[1] : '—';
}
function localDateTime(date) {
  return two(date.getDate()) + '.' + two(date.getMonth() + 1) + '.' + date.getFullYear() +
    ' ' + two(date.getHours()) + ':' + two(date.getMinutes());
}
function time(value) {
  if (value === null || value === undefined || value === '') return '—';
  var date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : localDateTime(date);
}
/* A legacy naive server timestamp has no timezone; show its wall time as such. */
function serverTime(value) {
  var match = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/.exec(String(value || ''));
  return match ? match[3] + '.' + match[2] + '.' + match[1] + ' ' + match[4] + ':' + match[5] : '—';
}
function countdown(ms) {
  if (!number(ms)) return null;
  var delta = ms - Date.now();
  if (delta <= 0) return 'resetting…';
  var minutes = Math.floor(delta / 60000);
  var days = Math.floor(minutes / 1440);
  var hours = Math.floor((minutes % 1440) / 60);
  var mins = minutes % 60;
  if (days > 0) return 'in ' + days + 'd ' + hours + 'h';
  if (hours > 0) return 'in ' + hours + 'h ' + mins + 'm';
  return 'in ' + mins + 'm';
}
/* Relative age of a timestamp, e.g. "just now", "7m ago", "3h ago". */
function relativeAge(ms) {
  if (!number(ms)) return null;
  var delta = Date.now() - ms;
  if (delta < 60000) return 'just now';
  var minutes = Math.floor(delta / 60000);
  if (minutes < 60) return minutes + 'm ago';
  var hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + 'h ago';
  return Math.floor(hours / 24) + 'd ago';
}
/* A snapshot older than this is reported as stale rather than current. */
var STALE_AFTER_MS = 15 * 60 * 1000;
/* Describe the snapshot instant.
   `utc` is the offset-aware generated_at_utc, so the browser can render it in
   the viewer's own timezone. A legacy naive value has no offset and would be
   misread as local time, so it is surfaced as raw server time instead. */
function snapshotLabel(utc, fallback) {
  var ms = utc ? new Date(utc).getTime() : NaN;
  if (Number.isNaN(ms)) {
    return { text: 'snapshot ' + serverTime(fallback) + ' (server time)', stale: true };
  }
  var zone = '';
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (error) { zone = ''; }
  var age = relativeAge(ms);
  return {
    text: 'snapshot ' + time(ms) + (zone ? ' · ' + zone : '') + (age ? ' · ' + age : ''),
    stale: Date.now() - ms > STALE_AFTER_MS,
  };
}
function providerName(provider) {
  var key = String(provider || 'unknown');
  var known = { codex: 'Codex', grok: 'Grok', grok_bot: 'Grok Bot', glm: 'GLM', ollama: 'Ollama', claude: 'Claude',
                antigravity: 'Antigravity', cursor: 'Cursor', nordrouter: 'NordRouter' };
  if (known[key]) return known[key];
  return key.charAt(0).toUpperCase() + key.slice(1);
}
/* Compact brand mark. Inline text so the page stays asset-free. */
function brandMark(provider) {
  var key = String(provider || '').toLowerCase();
  var marks = { codex: 'OI', grok: 'xAI', grok_bot: 'xAI', nordrouter: 'NR', glm: 'Z', ollama: 'OL',
                claude: 'CC', antigravity: 'AG', cursor: 'CU' };
  return marks[key] || key.slice(0, 2).toUpperCase();
}
/* Bar colour follows remaining capacity: green when comfortable, amber as the
   window fills, red as it approaches exhaustion. */
function severity(used) { return used >= 80 ? 'danger' : (used >= 50 ? 'warn' : 'ok'); }
function statusClass(status) {
  var key = String(status || 'unknown').toLowerCase();
  if (key === 'ok') return 'ok';
  if (key === 'stale') return 'stale';
  if (key === 'not_configured') return 'not_configured';
  if (key === 'unavailable') return 'unavailable';
  return 'unknown';
}
function statusText(status) {
  var key = String(status || '').toLowerCase();
  if (key === 'ok') return 'Active';
  if (key === 'stale') return 'Stale data';
  if (key === 'not_configured') return 'Not configured';
  if (key === 'unavailable') return 'Unavailable';
  return key ? key : 'Unknown';
}

/* ---------- limit rows ---------- */
function windowRow(container, window) {
  var row = node('div', undefined, container); row.className = 'win';
  row.setAttribute('data-provider', window.provider || 'unknown');
  var label = window.provider === 'grok_bot' ? 'Grok Bot' : (window.label || 'Quota');
  node('span', label, row).className = 'win-label';

  if (number(window.used_percentage)) {
    var track = node('div', undefined, row);
    track.className = 'track';
    track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    track.setAttribute('aria-valuenow', String(Math.round(window.used_percentage)));
    track.setAttribute('aria-label', label + ' used');
    var fill = node('div', undefined, track);
    fill.className = 'fill ' + severity(window.used_percentage);
    fill.style.width = Math.max(0, Math.min(100, window.used_percentage)) + '%';

    var val = node('span', undefined, row); val.className = 'win-val';
    node('span', window.used_percentage + '% used', val);
    if (window.remaining !== null && window.remaining !== undefined) {
      node('span', ' · ' + window.remaining + ' remaining', val).className = 'rem';
    } else if (number(window.remaining_percentage)) {
      node('span', ' · ' + window.remaining_percentage + '% left', val).className = 'rem';
    }

    var resetMs = window.next_reset_time_ms;
    var resetIso = window.next_reset_iso;
    if (resetMs || resetIso) {
      var when = countdown(resetMs);
      node('span', 'resets ' + time(resetMs || resetIso) + (when ? ' (' + when + ')' : ''), row).className = 'win-reset';
    }
    if (window.provider === 'grok' && Array.isArray(window.product_usage) && window.product_usage.length) {
      var productText = window.product_usage.map(function (item) {
        return (item.label || ('Product ' + item.product + ' (unknown)')) + ': ' + item.usage_percent.toFixed(1) + '%';
      }).join(' · ');
      node('span', productText + ' of shared credits (not tokens or separate limits)', row).className = 'win-reset';
    }
  } else {
    var emptyTrack = node('div', undefined, row); emptyTrack.className = 'track';
    node('div', undefined, emptyTrack).className = 'fill unknown';
    node('span', 'Used: —', row).className = 'win-val';
    node('p', 'No percentage reported for this window.', container).className = 'unavailable';
  }
}

function limitGroup(group, root) {
  var status = effectiveStatus(group);
  var card = node('div', undefined, root);
  card.className = 'limit-group' + (status === 'not_configured' ? ' subdued' : '');
  var head = node('div', undefined, card); head.className = 'limit-head';
  node('span', brandMark(group.provider), head).className = 'brandtile';
  node('span', providerName(group.provider), head).className = 'pname';
  if (group.account) node('span', group.account_label || group.account, head).className = 'acct';
  var chip = node('span', statusText(status), head);
  chip.className = 'chip ' + statusClass(status);
  group.windows.forEach(function (window) { windowRow(card, window); });
  // A placeholder account is only useful if it says how to become real.
  if (status === 'not_configured') {
    node('p', 'No second Codex login on this machine yet.', card).className = 'unavailable';
  }
}

/* A provider that reports a real percentage but no explicit status is live. */
function effectiveStatus(group) {
  if (group.status) return group.status;
  var hasPercentage = group.windows.some(function (window) { return number(window.used_percentage); });
  return hasPercentage ? 'ok' : 'unknown';
}

function groupQuotas(rows) {
  var groups = new Map();
  rows.forEach(function (row) {
    // The Bot relay is an independent window under the same Grok provider.
    var provider = row.provider === 'grok_bot' ? 'grok' : (row.provider || 'unknown');
    var account = row.account || '';
    var key = provider + '::' + account;
    if (!groups.has(key)) {
      groups.set(key, { provider: provider, account: account, account_label: row.account_label, status: row.status, windows: [] });
    }
    var group = groups.get(key);
    // An explicit non-ok status (stale, not_configured, unavailable) wins; a
    // plain ok only fills in when nothing has been recorded yet.
    if (row.status && row.status !== 'ok') group.status = row.status;
    else if (row.status === 'ok' && !group.status) group.status = row.status;
    group.windows.push(row);
  });
  // A failed Bot read must not make a live weekly Grok pool appear unavailable.
  groups.forEach(function (group) {
    if (group.provider === 'grok' && group.windows.some(function (window) {
      return window.status === 'ok' || (!window.status && number(window.used_percentage));
    })) group.status = 'ok';
  });
  return Array.from(groups.values());
}

/* Quota section order the user asked for: Codex accounts first (account 1,
   then account 2, each showing configured or unconfigured state), then Grok,
   then any remaining providers. NordRouter renders in its own section. */
var QUOTA_ORDER = ['codex', 'grok'];
function orderGroups(groups) {
  return groups.slice().sort(function (a, b) {
    var ia = QUOTA_ORDER.indexOf(a.provider);
    var ib = QUOTA_ORDER.indexOf(b.provider);
    if (ia === -1) ia = QUOTA_ORDER.length;
    if (ib === -1) ib = QUOTA_ORDER.length;
    if (ia !== ib) return ia - ib;
    return String(a.account || '').localeCompare(String(b.account || ''));
  });
}

function renderQuotas(data, models) {
  var rows = data.quotas || [];
  var root = $('quotas');
  root.replaceChildren();

  var codex = rows.filter(function (row) { return row.provider === 'codex'; });
  var codexRoot = $('codex');
  codexRoot.replaceChildren();
  if (codex.length) {
    orderGroups(groupQuotas(codex)).forEach(function (group) { limitGroup(group, codexRoot); });
  } else {
    node('p', 'No Codex snapshot available.', codexRoot).className = 'empty';
  }

  var others = rows.filter(function (row) { return row.provider !== 'nordrouter' && row.provider !== 'codex'; });
  if (!others.length) {
    node('p', 'No Grok or other quota snapshots available.', root).className = 'empty';
  } else {
    orderGroups(groupQuotas(others)).forEach(function (group) { limitGroup(group, root); });
  }

  var nr = rows.find(function (row) { return row.provider === 'nordrouter'; }) || {};
  var nrStatus = $('nr-status');
  if (nrStatus) {
    var text = nr.today_complete === false ? 'Today model list incomplete; spend may use a fallback' : '';
    nrStatus.textContent = text;
  }

  $('metrics').replaceChildren();
  [['Balance · current', 'balance_usd'], ['Spend today · current day', 'spend_today_usd'],
   ['Billed spend · ' + selectedDays + 'd', selectedDays === 30 ? 'spend_30d_usd' : 'spend_7d_usd']]
    .forEach(function (pair) {
      var box = node('div', undefined, $('metrics')); box.className = 'nr-cell';
      node('div', pair[0], box).className = 'k';
      var value = node('div', usd(nr[pair[1]]), box);
      value.className = 'v' + (number(nr[pair[1]]) ? '' : ' unknown');
    });
  topModels('today', nr.top_models_today);
  $('selected-top-label').textContent = 'Top-5 models · ' + selectedDays + 'd';
  topModels('week', (models.models || []).filter(function (row) { return row.source === 'nordrouter'; })
    .map(function (row) { return { id: row.model, tokens: row.totals.total, amount_usd: row.cost_usd }; }));

  var stamp = snapshotLabel(data.generated_at_utc, data.generated_at);
  var stampNode = $('stamp');
  stampNode.textContent = stamp.text;
  stampNode.className = 'stamp' + (stamp.stale ? ' stale' : '');
  return rows;
}

function topModels(id, rows) {
  var root = $(id); root.replaceChildren();
  if (!rows || !rows.length) { node('p', 'No model data available.', root).className = 'empty'; return; }
  var list = node('ol', undefined, root); list.className = 'top';
  rows.slice().sort(function (a, b) { return (b.amount_usd || 0) - (a.amount_usd || 0); }).slice(0, 5)
    .forEach(function (row) {
      var item = node('li', undefined, list);
      var name = node('span', row.id || 'Unknown', item); name.className = 'id'; name.title = row.id || 'Unknown';
      node('span', usd(row.amount_usd) + ' · ' + tokens(row.tokens) + ' tok', item).className = 'amt';
    });
}

/* ---------- summary ---------- */
function stripItem(root, label, value, sub, unknown) {
  var box = node('div', undefined, root); box.className = 'strip-item';
  node('div', label, box).className = 'k';
  var v = node('div', value, box);
  v.className = 'v' + (unknown ? ' unknown' : '');
  if (sub) node('div', sub, box).className = 's';
  return box;
}

function renderHero(payload) {
  var sources = payload.sources || {};
  var direct = sources.nordrouter || {};
  var gateway = sources.openclaw || {};
  var root = $('hero');
  root.replaceChildren();
  var full = direct.complete && gateway.complete;
  stripItem(root, 'Total tokens · ' + selectedDays + 'd', full ? tokens(payload.totals.total) : '—',
    full ? null : 'incomplete source · total unavailable', !full);
  stripItem(root, 'NordRouter billed cost · ' + selectedDays + 'd', usd(direct.billed_cost_usd),
    'actual direct account window', !number(direct.billed_cost_usd));
  stripItem(root, 'OpenClaw estimated cost · ' + selectedDays + 'd', usd(gateway.estimated_cost_usd),
    'model-price estimate' + (gateway.unpriced_models ? ' · partial: ' + gateway.unpriced_models + ' unpriced models' : '') +
    ' · excludes NordRouter route', !number(gateway.estimated_cost_usd));
  stripItem(root, 'OpenClaw NordRouter comparison · ' + selectedDays + 'd',
    tokens(sources.openclaw_nordrouter_comparison_tokens), 'excluded from canonical total and cost', false);
  if (full) {
    var pills = node('div', undefined, root); pills.className = 'pillrow';
    node('span', 'direct NordRouter ' + tokens(direct.tokens), pills).className = 'pill';
    node('span', 'other OpenClaw ' + tokens(gateway.tokens), pills).className = 'pill';
  }
}

/* ---------- history chart ---------- */
var lastHistoryData = null;
var SVG_NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, parent) {
  var element = document.createElementNS(SVG_NS, tag);
  Object.keys(attrs || {}).forEach(function (key) { element.setAttribute(key, attrs[key]); });
  if (parent) parent.append(element);
  return element;
}
function renderHistory(payload) {
  lastHistoryData = payload;
  var root = $('history');
  root.replaceChildren();
  var byDate = new Map();
  (payload.models || []).forEach(function (model) {
    if (model.source !== 'openclaw' || String(model.provider).toLowerCase() === 'nordrouter') return;
    (model.daily || []).forEach(function (day) {
      byDate.set(day.date, (byDate.get(day.date) || 0) + day.total);
    });
  });
  ((payload.source_daily || {}).nordrouter || []).forEach(function (day) {
    byDate.set(day.date, (byDate.get(day.date) || 0) + day.tokens);
  });
  var series = Array.from(byDate, function (item) { return { date: item[0], total_tokens: item[1], cost_usd: null }; })
    .sort(function (a, b) { return a.date.localeCompare(b.date); });
  var peak = series.reduce(function (max, day) { return Math.max(max, day.total_tokens); }, 0);
  if (!series.length || peak <= 0) {
    node('p', 'No historical usage data available.', root).className = 'empty';
    return;
  }
  var width = Math.max(320, root.clientWidth || 900);
  var height = 150, padL = 44, padR = 8, padT = 10, padB = 22;
  var plotW = width - padL - padR, plotH = height - padT - padB;
  var svg = svgEl('svg', { class: 'chart', viewBox: '0 0 ' + width + ' ' + height, preserveAspectRatio: 'none', role: 'img' }, root);
  svgEl('title', {}, svg).textContent = 'Daily token usage for the last ' + series.length + ' days';
  for (var g = 0; g <= 2; g++) {
    var y = padT + (plotH * g) / 2;
    svgEl('line', { x1: padL, y1: y, x2: width - padR, y2: y, stroke: '#343434', 'stroke-width': 1 }, svg);
    var label = svgEl('text', { x: padL - 6, y: y + 3, fill: '#8a8a8a', 'font-size': 9, 'text-anchor': 'end' }, svg);
    label.textContent = tokens(peak * (1 - g / 2));
  }
  var slot = plotW / series.length;
  var barW = Math.max(2, Math.min(22, slot * 0.6));
  var axisLabels = Math.min(6, Math.max(2, Math.floor(plotW / 90)));
  var labelEvery = Math.ceil(series.length / axisLabels);
  series.forEach(function (day, index) {
    var ratio = day.total_tokens / peak;
    var barH = Math.max(ratio > 0 ? 1.5 : 0, plotH * ratio);
    var x = padL + slot * index + (slot - barW) / 2;
    var rect = svgEl('rect', {
      x: x, y: padT + plotH - barH, width: barW, height: barH, rx: 1.5, fill: '#7aa2f7', opacity: 0.9,
    }, svg);
    svgEl('title', {}, rect).textContent = dateOnly(day.date) + ' · ' + day.total_tokens.toLocaleString() + ' tokens';
    if (index === 0 || index === series.length - 1 || index % labelEvery === 0) {
      var xl = svgEl('text', { x: padL + slot * index + slot / 2, y: height - 6, fill: '#8a8a8a', 'font-size': 9, 'text-anchor': 'middle' }, svg);
      xl.textContent = dateOnly(day.date);
    }
  });
  var legend = node('div', undefined, root); legend.className = 'legend';
  var item = node('span', undefined, legend);
  node('span', undefined, item).className = 'swatch';
  node('span', 'daily tokens · ' + series.length + ' days · peak ' + tokens(peak), item);
  node('span', 'total ' + tokens(series.reduce(function (sum, day) { return sum + day.total_tokens; }, 0)) + ' tokens',
    node('span', undefined, legend));
}

/* ---------- model table ---------- */
var modelSortColumns = ['source', 'model', 'input', 'output', 'cache_read', 'cache_write', 'total', 'usd'];
var modelSort = { key: 'total', direction: 'default' };
var lastModelsData = null;
function modelSortValue(row, key) {
  if (key === 'source') return row.provider || null;
  if (key === 'model') return row.model || null;
  var value = key === 'usd' ? row.cost_usd : row.totals && row.totals[key];
  return number(value) ? value : null;
}
function sortedModels(rows) {
  var key = modelSort.direction === 'default' ? 'total' : modelSort.key;
  var direction = modelSort.direction === 'ascending' ? 1 : -1;
  return rows.map(function (row, index) { return { row: row, index: index }; }).sort(function (a, b) {
    var av = modelSortValue(a.row, key), bv = modelSortValue(b.row, key);
    if (av === null || bv === null) return av === null && bv === null ? a.index - b.index : av === null ? 1 : -1;
    var comparison = typeof av === 'string' ? av.localeCompare(bv, undefined, { sensitivity: 'base' }) : av - bv;
    return comparison ? comparison * direction : a.index - b.index;
  }).map(function (item) { return item.row; });
}
function syncModelSortHeaders() {
  var active = modelSort.direction === 'default' ? 'total' : modelSort.key;
  modelSortColumns.forEach(function (key) {
    var direction = key === active ? (modelSort.direction === 'ascending' ? 'ascending' : 'descending') : 'none';
    $('model-sort-head-' + key).setAttribute('aria-sort', direction);
    $('model-sort-mark-' + key).textContent = direction === 'none' ? '↕' : direction === 'ascending' ? '↑' : '↓';
    $('model-sort-' + key).title = key === modelSort.key && modelSort.direction === 'descending' ? 'Sort ascending'
      : key === modelSort.key && modelSort.direction === 'ascending' ? 'Reset to total tokens descending' : 'Sort descending';
  });
}
function renderModels(data) {
  lastModelsData = data;
  var meta = data.meta || {};
  $('model-stamp').textContent = 'snapshot ' +
    (meta.generated_at_utc ? time(meta.generated_at_utc) : serverTime(meta.generated_at) + ' (server time)') +
    (meta.start_date && meta.end_date ? ' · ' + dateOnly(meta.start_date) + ' → ' + dateOnly(meta.end_date) : '') +
    ' · OpenClaw ' + (meta.openclaw_status || 'unknown') + ' · NordRouter ' + (meta.nordrouter_status || 'unknown');
  var body = $('models');
  body.replaceChildren();
  var models = sortedModels((data.models || []).filter(function (row) { return row.source === 'openclaw'; }));
  if (!models.length) {
    var emptyRow = node('tr', undefined, body);
    var emptyCell = node('td', 'No model data available.', emptyRow);
    emptyCell.colSpan = 8;
    return;
  }
  var top = models.reduce(function (max, row) {
    var total = row.totals && number(row.totals.total) ? row.totals.total : 0;
    return Math.max(max, total);
  }, 0);
  models.forEach(function (row) {
    var tr = node('tr', undefined, body);
    var srcCell = node('td', undefined, tr);
    node('span', row.provider || 'unknown', srcCell).className = 'src';
    var modelCell = node('td', undefined, tr);
    node('span', row.model || '—', modelCell);
    if (row.daily && row.daily.length) {
      var detail = node('details', undefined, modelCell);
      node('summary', row.daily.length + ' daily rows', detail);
      row.daily.forEach(function (day) {
        node('div', dateOnly(day.date) + ' · ' + tokens(day.total) + ' tokens', detail).className = 'src';
      });
    }
    ['input', 'output', 'cache_read', 'cache_write'].forEach(function (field) {
      var cell = node('td', tokens(row.totals && row.totals[field]), tr); cell.className = 'num';
    });
    var total = row.totals && number(row.totals.total) ? row.totals.total : null;
    var tokCell = node('td', tokens(total), tr); tokCell.className = 'num';
    if (total && top > 0) {
      var mini = node('div', undefined, tokCell); mini.className = 'bar-mini';
      node('i', undefined, mini).style.width = Math.max(2, Math.round((total / top) * 100)) + '%';
    }
    var costCell = node('td', usd(row.cost_usd), tr); costCell.className = 'num';
  });
}

function renderCosts(data) {
  var body = $('cost-models'); body.replaceChildren();
  var rows = (data.models || []).filter(function (row) { return row.source === 'nordrouter'; });
  rows.sort(function (a, b) { return (b.cost_usd || 0) - (a.cost_usd || 0); });
  if (!rows.length) {
    var empty = node('tr', undefined, body); node('td', 'No direct NordRouter model data available.', empty).colSpan = 3;
  }
  rows.forEach(function (row) {
    var tr = node('tr', undefined, body);
    node('td', row.model, tr);
    node('td', tokens(row.totals.total), tr).className = 'num';
    node('td', usd(row.cost_usd), tr).className = 'num';
  });
}

/* ---------- loading ---------- */
function json(url, options) {
  var controller = new AbortController();
  var timer = setTimeout(function () { controller.abort(); }, 180000);
  return fetch(url, Object.assign({ cache: 'no-store', signal: controller.signal }, options || {}))
    .then(function (response) {
      if (!response.ok) throw new Error('HTTP ' + response.status);
      if (response.headers && response.headers.get('X-Dashboard-Refresh') === 'stale')
        throw new Error('provider refresh failed; cached snapshot retained');
      return response.json();
    })
    .finally(function () { clearTimeout(timer); });
}

var busy = false;
var selectedDays = 30;
function setPeriodButtons() {
  [30, 7].forEach(function (days) {
    var button = $('period-' + days);
    button.setAttribute('aria-pressed', String(days === selectedDays));
    button.disabled = busy;
  });
}
function renderPeriod(models, quotas) {
  renderHero(models);
  renderQuotas(quotas, models);
  renderHistory(models);
  renderCosts(models);
  renderModels(models);
}
function reload(force, nextDays) {
  if (busy) return;
  busy = true;
  var button = $('refresh');
  button.disabled = true; button.classList.add('busy');
  setPeriodButtons();
  $('error').textContent = '';
  var days = nextDays || selectedDays;
  var prefix = nextDays ? Promise.resolve() : json('/api/v1/display/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: force ? 'force_button' : 'auto_refresh', view: days + 'd', device_id: 'web_dashboard' }),
      });

  prefix.then(function () {
    return Promise.all([
      json('/api/v1/quotas'),
      json('/api/v1/model-breakdown?days=' + days + '&daily=true'),
    ]);
  }).then(function (results) {
    if (results[1].meta.days !== days) throw new Error('period mismatch');
    selectedDays = days;
    renderPeriod(results[1], results[0]);
  }).catch(function (error) {
    $('error').textContent = 'Selected period unavailable: ' + error.message + '. Last displayed data retained.';
  }).finally(function () {
    busy = false; button.disabled = false; button.classList.remove('busy');
    setPeriodButtons();
  });
}

$('refresh').addEventListener('click', function () { reload(true); });
modelSortColumns.forEach(function (key) {
  $('model-sort-' + key).addEventListener('click', function () {
    modelSort = key !== modelSort.key || modelSort.direction === 'default' ? { key: key, direction: 'descending' }
      : modelSort.direction === 'descending' ? { key: key, direction: 'ascending' }
      : { key: 'total', direction: 'default' };
    syncModelSortHeaders();
    if (lastModelsData) renderModels(lastModelsData);
  });
});
syncModelSortHeaders();
['30', '7'].forEach(function (days) {
  $('period-' + days).addEventListener('click', function () {
    if (Number(days) !== selectedDays) reload(false, Number(days));
  });
});
window.addEventListener('resize', function () {
  if (lastHistoryData) renderHistory(lastHistoryData);
});
setPeriodButtons();
reload(false);
setInterval(function () { reload(false); }, 5 * 60000);
</script>
</body>
</html>'''
