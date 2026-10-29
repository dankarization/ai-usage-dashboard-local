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
from table_sort import TABLE_SORT_JS

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
.brandtile svg{display:block;width:16px;height:16px;fill:currentColor;color:var(--text)}
.quota-title{display:flex;align-items:center;gap:10px;font-size:15px;font-weight:600;letter-spacing:0;text-transform:none;color:var(--text)}
.brandmark{display:inline-flex;align-items:center;justify-content:center;width:24px;height:24px;flex:none;color:var(--text)}
.brandmark.codex{color:#10a37f}
.brandmark svg{display:block;width:24px;height:24px;fill:currentColor}
.plan-label{font-size:11px;font-weight:400;color:var(--muted);margin-left:auto}
.plan-label + .chip{margin-left:0}
.pname{font-size:13px;font-weight:600}
.acct{font-family:var(--font-code);font-size:11px;color:var(--faint)}
.chip{margin-left:auto;font-size:11px;font-weight:500;padding:2px 8px;border-radius:var(--pill);border:1px solid var(--line);color:var(--muted);background:var(--raised);white-space:nowrap}
.chip.ok{color:var(--green);border-color:#2f5c4b;background:#1d2b26}
.chip.warn,.chip.stale{color:var(--amber);border-color:#5a4a1c;background:#2a2416}
.chip.danger{color:var(--red);border-color:#5c3030;background:#2b1d1d}
.chip.unknown,.chip.not_configured,.chip.unavailable{color:var(--neutral);border-color:var(--line);background:var(--raised)}

.win{display:grid;grid-template-columns:96px minmax(0,1fr) auto;gap:var(--s3);align-items:center;padding:3px 0}
#codex .win,#quotas .limit-group[data-provider="grok"] .win{grid-template-columns:58px minmax(0,1fr) auto;column-gap:8px}
.win-label{font-size:12px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.track{height:8px;border-radius:var(--pill);background:#171717;border:1px solid var(--line-soft);overflow:hidden}
.fill{height:100%;border-radius:var(--pill);transition:width 400ms ease}
.fill.ok{background:var(--green)} .fill.warn{background:var(--amber)} .fill.danger{background:var(--red)}
.fill.full{background:#cc2850;box-shadow:inset 0 0 0 1px #ee6e8999}
.fill.unknown{background:repeating-linear-gradient(135deg,#3a3a3a 0 4px,#2c2c2c 4px 8px)}
.fill.segmented{display:flex;overflow:hidden;background:none}
.product-segment{height:100%;flex:none}
.product-legend{grid-column:2/4;display:flex;flex-wrap:wrap;gap:3px 12px;min-width:0}
.product-legend-item{display:inline-flex;align-items:baseline;gap:5px;min-width:0;color:var(--faint);font-size:11px;overflow-wrap:anywhere}
.product-swatch{width:9px;height:9px;border-radius:2px;flex:none;align-self:center}
.win-val{font-size:12px;font-variant-numeric:tabular-nums;color:var(--text);white-space:nowrap}
.win-val .rem{color:var(--faint);font-size:11px}
.win-reset{grid-column:2/4;min-width:0;overflow-wrap:anywhere;font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}
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
.history-head{align-items:center}
.history-scale{margin-left:auto;font-size:11px;padding:5px 9px}
svg.chart{display:block;width:100%;height:300px}
.chart-summary{color:var(--faint);font-size:11px;padding:6px 0 0}
.history-table{margin-top:var(--s3);padding-top:var(--s3);border-top:1px solid var(--line-soft)}
.history-tooltip{position:fixed;z-index:30;visibility:hidden;pointer-events:none;max-width:min(280px,calc(100vw - 16px));padding:7px 10px;border-radius:var(--radius-sm);background:var(--raised);color:var(--text);font-size:12px;line-height:1.4;box-shadow:0 0 0 0.67px var(--line),0 4px 16px #0008;overflow-wrap:anywhere}
.model-name{display:inline-flex;align-items:baseline;gap:7px;min-width:0;overflow-wrap:anywhere}
.model-swatch{width:9px;height:9px;border-radius:2px;display:inline-block;flex:none;align-self:center}

/* ---------- table ---------- */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:6px var(--s3) 6px 0;border-bottom:1px solid var(--line-soft);overflow-wrap:anywhere;vertical-align:middle}
th{color:var(--faint);font-size:11px;text-transform:uppercase;letter-spacing:.04em;font-weight:500;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
.bucket-row td{vertical-align:top}
.bucket-row td:nth-child(2){min-width:250px}
.bucket-detail-row td{vertical-align:top}
.bucket-detail-row td:nth-child(2){padding-left:12px}
.bucket-day-route td:nth-child(2){padding-left:24px}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.src{font-family:var(--font-code);font-size:11px;color:var(--faint)}
.sort-btn{appearance:none;border:0;background:none;color:inherit;font:inherit;text-transform:inherit;letter-spacing:inherit;padding:2px 0;cursor:pointer;white-space:nowrap}
.sort-btn:hover{color:var(--text)}
.sort-btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:2px}
.sort-mark{display:inline-block;min-width:12px;margin-left:3px;color:var(--accent)}
.bar-mini{height:8px;border-radius:var(--pill);background:#171717;overflow:hidden;margin-top:4px;max-width:180px;border:1px solid var(--line-soft)}
.bar-mini i{display:block;height:100%;background:var(--accent)}
.token-compare{display:flex;align-items:baseline;justify-content:flex-end;gap:6px;flex-wrap:wrap}
.token-compare s{color:var(--faint)}
.token-compare .direct{color:var(--text);font-weight:600}
.cost-kind{display:block;color:var(--faint);font-size:10px;font-weight:400}

/* ---------- misc ---------- */
#error{color:#f0a8a2;font-size:12px;margin:var(--s3) 0 0}
.empty{color:var(--faint);font-size:12px;font-style:italic;margin:6px 0}
.foot{margin-top:var(--s6);color:var(--faint);font-size:11px;text-align:center}
.foot p{margin:0}
.foot .note{margin-bottom:var(--s2)}

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
  .product-legend{grid-column:1}
  #codex .win,#quotas .limit-group[data-provider="grok"] .win{grid-template-columns:58px minmax(0,1fr);column-gap:8px}
  #codex .win-val,#quotas .limit-group[data-provider="grok"] .win-val{grid-column:2;order:0}
  #codex .win-reset,#quotas .limit-group[data-provider="grok"] .win-reset,
  #quotas .limit-group[data-provider="grok"] .product-legend{grid-column:2}
  .win-val{order:2}
  svg.chart{height:260px}
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
      <button id="period-30" class="btn" type="button" aria-pressed="false">30d</button>
      <button id="period-7" class="btn" type="button" aria-pressed="true">7d</button>
      <button id="period-1" class="btn" type="button" aria-pressed="false">1d</button>
    </div>
    <button id="refresh" class="btn primary" type="button"><span class="pulse" aria-hidden="true"></span>Refresh</button>
  </div>
</header>

<main class="wrap">
  <p id="error" role="status" aria-live="polite"></p>

  <section aria-labelledby="h-hero">
    <div class="sec-head"><h2 id="h-hero">Overview</h2><p class="note" id="overview-note">Canonical direct NordRouter + agent usage, summed per model</p></div>
    <div id="hero" class="strip"></div>
  </section>

  <section aria-labelledby="h-codex">
    <div class="sec-head"><h2 id="h-codex" class="quota-title"><span id="codex-brand"></span>OpenAI (Codex login)</h2></div>
    <div class="panel flat"><div id="codex"></div></div>
  </section>

  <section aria-labelledby="h-grok">
    <div class="sec-head"><h2 id="h-grok" class="quota-title"><span id="grok-brand"></span>xAI Grok</h2></div>
    <div class="panel flat"><div id="quotas"></div></div>
  </section>

  <section aria-labelledby="h-nord">
    <div class="sec-head"><h2 id="h-nord">NordRouter</h2></div>
    <div class="panel">
      <div id="metrics" class="nr-grid"></div>
      <div class="two-col">
        <div><h3>Top-5 models today</h3><div id="today"></div></div>
        <div><h3 id="selected-top-label">Top-5 models</h3><div id="week"></div></div>
      </div>
    </div>
  </section>

  <section aria-labelledby="h-history">
    <div class="sec-head history-head"><h2 id="h-history">Usage history</h2><button id="history-scale" class="btn history-scale" type="button" aria-pressed="false">Scale: linear</button></div>
    <div class="panel">
      <div id="history" class="chart-wrap"></div>
      <div class="history-table">
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
            <th id="model-sort-head-total" class="num" aria-sort="descending"><button id="model-sort-total" class="sort-btn" type="button">Tokens<span id="model-sort-mark-total" class="sort-mark" aria-hidden="true">↓</span></button></th>
            <th id="model-sort-head-usd" class="num"><button id="model-sort-usd" class="sort-btn" type="button">USD<span id="model-sort-mark-usd" class="sort-mark" aria-hidden="true">↕</span></button></th>
          </tr></thead>
          <tbody id="models"></tbody>
          </table>
        </div>
      </div>
    </div>
  </section>

  <footer class="foot"><p class="note" id="models-note">One row per model: spellings that bill as one model are summed. NordRouter bills every agent, so its per-source token count is unknown; the crossed-out agent total → direct figure is the account number. Input/output/cache on that row are agent comparisons, not direct token types. Model rows are not account totals.</p><p>Auto-refresh every 5m · data never leaves this machine</p></footer>
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
var BRAND_MARKS = {
  "codex": {
    "label": "OpenAI (Codex)",
    "viewBox": "100 100 411 411",
    "paths": [
      {
        "fill-rule": "evenodd",
        "clip-rule": "evenodd",
        "d": "M252.794 108.802C289.191 99.0484 326.265 110.305 351.148 135.135C385.113 126.072 422.85 134.862 449.492 161.505C476.136 188.149 484.925 225.888 475.862 259.85V259.854C500.696 284.735 511.95 321.81 502.198 358.207C492.447 394.602 464.161 421.084 430.215 430.217C421.083 464.162 394.603 492.448 358.206 502.199C321.812 511.951 284.734 500.693 259.852 475.864C225.887 484.927 188.15 476.137 161.507 449.495C134.864 422.851 126.073 385.111 135.136 351.149C110.304 326.266 99.0496 289.192 108.801 252.795C118.552 216.4 146.84 189.918 180.784 180.785C189.917 146.841 216.396 118.553 252.794 108.802ZM374.292 407.145C374.292 411.271 372.092 415.086 368.517 417.148L283.723 466.102C302.487 480.585 327.555 486.459 352.217 479.852C386.997 470.532 410.068 439.312 410.555 405.006V317.717C410.555 315.08 409.125 312.621 406.843 311.303L374.292 292.509V407.145ZM251.868 415.897C248.296 417.959 243.893 417.959 240.317 415.897L155.526 366.942C152.366 390.436 159.811 415.08 177.866 433.136H177.863C203.325 458.594 241.896 462.962 271.85 446.232L347.449 402.586C349.735 401.268 351.148 398.8 351.148 396.163V358.579L251.868 415.897ZM368.602 220.628C366.319 219.309 363.474 219.318 361.191 220.637L328.641 239.431L427.921 296.749C431.496 298.811 433.697 302.627 433.697 306.752V404.661C455.622 395.654 473.244 376.881 479.851 352.218C489.169 317.442 473.668 281.85 444.201 264.274L368.602 220.628ZM177.303 206.34C155.377 215.348 137.756 234.122 131.148 258.783C121.832 293.561 137.331 329.153 166.799 346.727L242.398 390.373C244.68 391.692 247.525 391.684 249.807 390.366L282.357 371.572L183.078 314.253C179.504 312.189 177.303 308.375 177.303 304.251V206.34ZM259.849 279.145V331.858L305.5 358.213L351.15 331.858V279.145L305.5 252.789L259.849 279.145ZM327.276 144.9C308.512 130.418 283.445 124.543 258.782 131.15C224.002 140.471 200.931 171.691 200.445 205.995V293.286C200.445 295.923 201.875 298.381 204.158 299.7L236.707 318.493V203.856C236.707 199.731 238.909 195.916 242.483 193.853L327.276 144.9ZM433.137 177.867C407.675 152.407 369.103 148.038 339.149 164.769L263.55 208.415C261.265 209.734 259.852 212.202 259.852 214.838V252.423L359.132 195.105C362.703 193.041 367.108 193.041 370.682 195.105L455.473 244.06C458.635 220.567 451.189 195.922 433.135 177.867H433.137Z",
        "fill": "currentColor"
      }
    ]
  },
  "grok": {
    "label": "Grok",
    "viewBox": "0 0 34 33",
    "paths": [
      {
        "d": "M13.2371 21.0407L24.3186 12.8506C24.8619 12.4491 25.6384 12.6057 25.8973 13.2294C27.2597 16.5185 26.651 20.4712 23.9403 23.1851C21.2297 25.8989 17.4581 26.4941 14.0108 25.1386L10.2449 26.8843C15.6463 30.5806 22.2053 29.6665 26.304 25.5601C29.5551 22.3051 30.562 17.8683 29.6205 13.8673L29.629 13.8758C28.2637 7.99809 29.9647 5.64871 33.449 0.844576C33.5314 0.730667 33.6139 0.616757 33.6964 0.5L29.1113 5.09055V5.07631L13.2343 21.0436",
        "fill": "currentColor"
      },
      {
        "d": "M10.9503 23.0313C7.07343 19.3235 7.74185 13.5853 11.0498 10.2763C13.4959 7.82722 17.5036 6.82767 21.0021 8.2971L24.7595 6.55998C24.0826 6.07017 23.215 5.54334 22.2195 5.17313C17.7198 3.31926 12.3326 4.24192 8.67479 7.90126C5.15635 11.4239 4.0499 16.8403 5.94992 21.4622C7.36924 24.9165 5.04257 27.3598 2.69884 29.826C1.86829 30.7002 1.0349 31.5745 0.36364 32.5L10.9474 23.0341",
        "fill": "currentColor"
      }
    ]
  }
};

/* Official SVG geometry is embedded locally; provenance: assets/vendor-marks.md. */
function brandMark(provider, parent) {
  var key = String(provider || '').toLowerCase();
  var official = BRAND_MARKS[key === 'grok_bot' ? 'grok' : key];
  var tile = node('span', undefined, parent); tile.className = official ? 'brandmark ' + (key === 'grok_bot' ? 'grok' : key) : 'brandtile';
  tile.setAttribute('role', 'img');
  tile.setAttribute('aria-label', official ? official.label : providerName(key));
  if (official) {
    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', official.viewBox);
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    official.paths.forEach(function (attributes) {
      var path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      Object.keys(attributes).forEach(function (name) { path.setAttribute(name, attributes[name]); });
      svg.append(path);
    });
    tile.append(svg);
    return tile;
  }
  var marks = { nordrouter: 'NR', glm: 'Z', ollama: 'OL',
                claude: 'CC', antigravity: 'AG', cursor: 'CU' };
  tile.textContent = marks[key] || key.slice(0, 2).toUpperCase();
  return tile;
}
/* Bar colour follows remaining capacity: green when comfortable, amber as the
   window fills, red as it approaches exhaustion. */
function severity(used) { return used === 100 ? 'full' : (used >= 80 ? 'danger' : (used >= 50 ? 'warn' : 'ok')); }
function grokProducts(window) {
  var products = window.product_usage;
  var total = window.used_percentage;
  if (!Array.isArray(products) || !products.length || !number(total) || total < 0 || total > 100) return null;
  var sum = 0;
  for (var i = 0; i < products.length; i++) {
    if (!products[i] || !number(products[i].usage_percent) || products[i].usage_percent < 0) return null;
    sum += products[i].usage_percent;
  }
  // The breakdown must account for the same shared pool. Do not stretch or
  // clip product percentages when a snapshot is incomplete or inconsistent.
  return Math.abs(sum - total) < 0.001 ? products : null;
}
function productColor(level, index) {
  if (level === 'full') return 'hsl(' + (346 + index * 5 % 12) + ' ' + (67 + index * 7 % 15) + '% ' + (48 + index * 11 % 16) + '%)';
  var family = level === 'danger' ? [0, 12] : (level === 'warn' ? [22, 27] : [135, 32]);
  var hue = family[0] + (index * 17 % family[1]);
  return 'hsl(' + hue + ' ' + (62 + index * 11 % 19) + '% ' + (43 + index * 13 % 24) + '%)';
}
function productLabel(item) {
  if (typeof item.label === 'string' && item.label.trim()) return item.label.trim();
  return item.product === null || item.product === undefined ? 'Unknown product' :
    'Product ' + item.product + ' (unknown)';
}
function statusClass(status) {
  var key = String(status || 'unknown').toLowerCase();
  if (key === 'ok') return 'ok';
  if (key === 'stale' || key === 'cached') return 'stale';
  if (key === 'not_configured') return 'not_configured';
  if (key === 'unavailable') return 'unavailable';
  return 'unknown';
}
function statusText(status) {
  var key = String(status || '').toLowerCase();
  if (key === 'ok') return 'Active';
  if (key === 'stale') return 'Stale data';
  if (key === 'cached') return 'Saved snapshot';
  if (key === 'not_configured') return 'Not configured';
  if (key === 'unavailable') return 'Unavailable';
  return key ? key : 'Unknown';
}

/* ---------- limit rows ---------- */
function windowRow(container, window) {
  var row = node('div', undefined, container); row.className = 'win';
  row.setAttribute('data-provider', window.provider || 'unknown');
  var label = window.provider === 'grok_bot' ? 'Grok Bot' : (window.label || 'Quota');
  // Provider quota windows are independent of the selected dashboard period.
  if (label === '7d') label = 'Weekly';
  if (label === '30d') label = 'Monthly';
  node('span', label, row).className = 'win-label';

  if (number(window.used_percentage)) {
    var track = node('div', undefined, row);
    track.className = 'track';
    track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    track.setAttribute('aria-valuenow', String(Math.round(window.used_percentage)));
    track.setAttribute('aria-label', label + ' used');
    var level = severity(window.used_percentage);
    var products = window.provider === 'grok' ? grokProducts(window) : null;
    var fill = node('div', undefined, track);
    fill.className = 'fill ' + level + (products ? ' segmented' : '');
    fill.style.width = Math.max(0, Math.min(100, window.used_percentage)) + '%';
    if (products) {
      track.className += ' product-track';
      products.forEach(function (item, index) {
        if (item.usage_percent <= 0) return;
        var segment = node('span', undefined, fill);
        segment.className = 'product-segment';
        segment.style.width = item.usage_percent / window.used_percentage * 100 + '%';
        segment.style.backgroundColor = productColor(level, index);
        segment.setAttribute('aria-hidden', 'true');
      });
      track.setAttribute('aria-valuetext', window.used_percentage + '% used: ' + products.map(function (item) {
        return productLabel(item) + ' ' + item.usage_percent + '%';
      }).join(', '));
    }

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
    if (products) {
      var legend = node('div', undefined, row); legend.className = 'product-legend';
      products.forEach(function (item, index) {
        var entry = node('span', undefined, legend); entry.className = 'product-legend-item';
        var swatch = node('span', undefined, entry); swatch.className = 'product-swatch';
        swatch.style.backgroundColor = productColor(level, index);
        swatch.setAttribute('aria-hidden', 'true');
        node('span', productLabel(item) + ': ' + item.usage_percent + '%', entry);
      });
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
  card.setAttribute('data-provider', group.provider);
  var head = node('div', undefined, card); head.className = 'limit-head';
  if (!((group.provider === 'codex' && root === $('codex')) || (group.provider === 'grok' && root === $('quotas')))) {
    brandMark(group.provider, head);
    node('span', providerName(group.provider), head).className = 'pname';
  }
  if (group.account) node('span', group.account_label || group.account, head).className = 'acct';
  var plan = group.provider === 'codex' ? codexPlanLabel(group.plan_type) : null;
  if (plan) node('span', plan, head).className = 'plan-label';
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

function codexPlanLabel(plan) {
  var labels = { prolite: 'Pro Lite', pro: 'Pro', pro500: 'Pro500', plus: 'Plus' };
  return Object.prototype.hasOwnProperty.call(labels, plan) ? labels[plan] : null;
}

function groupQuotas(rows) {
  var groups = new Map();
  rows.forEach(function (row) {
    // The Bot relay is an independent window under the same Grok provider.
    var provider = row.provider === 'grok_bot' ? 'grok' : (row.provider || 'unknown');
    var account = row.account || '';
    var key = provider + '::' + account;
    if (!groups.has(key)) {
      groups.set(key, { provider: provider, account: account, account_label: row.account_label, plan_type: row.plan_type, status: row.status, windows: [] });
    }
    var group = groups.get(key);
    if (group.plan_type !== row.plan_type) group.plan_type = null;
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

function renderQuotas(data, models, pending) {
  [['codex', 'codex-brand'], ['grok', 'grok-brand']].forEach(function (pair) {
    var holder = $(pair[1]);
    holder.replaceChildren();
    brandMark(pair[0], holder);
  });
  var rows = data.quotas || [];
  var root = $('quotas');
  root.replaceChildren();

  var codex = rows.filter(function (row) { return row.provider === 'codex'; });
  var codexRoot = $('codex');
  codexRoot.replaceChildren();
  if (codex.length) {
    orderGroups(groupQuotas(codex)).forEach(function (group) { limitGroup(group, codexRoot); });
  } else {
    node('p', pending && pending.codex ? 'Loading Codex quota…' : 'No Codex snapshot available.', codexRoot).className = 'empty';
  }

  var others = rows.filter(function (row) { return row.provider !== 'nordrouter' && row.provider !== 'codex'; });
  if (!others.length) {
    node('p', pending && (pending.grok || pending.grok_bot) ? 'Loading provider quotas…' :
      'No Grok or other quota snapshots available.', root).className = 'empty';
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
  var billedSpend = selectedDays === 1 ? (models.sources || {}).nordrouter : null;
  [['Balance · current', nr.balance_usd], ['Spend today · current day', nr.spend_today_usd],
   ['Billed spend', selectedDays === 1 ? billedSpend && billedSpend.billed_cost_usd :
     nr[selectedDays === 30 ? 'spend_30d_usd' : 'spend_7d_usd']]]
    .forEach(function (pair) {
      var box = node('div', undefined, $('metrics')); box.className = 'nr-cell';
      node('div', pair[0], box).className = 'k';
      var value = node('div', usd(pair[1]), box);
      value.className = 'v' + (number(pair[1]) ? '' : ' unknown');
    });
  topModels('today', nr.top_models_today);
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
  var hermes = sources.hermes || {};
  var agents = [gateway, hermes];
  var agentPriced = agents.filter(function (source) { return number(source.estimated_cost_usd); });
  var agentCost = agentPriced.length
    ? agentPriced.reduce(function (sum, source) { return sum + source.estimated_cost_usd; }, 0) : null;
  var unpriced = agents.reduce(function (sum, source) { return sum + (source.unpriced_models || 0); }, 0);
  var root = $('hero');
  root.replaceChildren();
  var full = direct.complete && gateway.complete && hermes.complete;
  stripItem(root, 'Total tokens', full ? tokens(payload.totals.total) : '—',
    full ? null : 'incomplete source · total unavailable', !full);
  stripItem(root, 'NordRouter billed cost', usd(direct.billed_cost_usd),
    null, !number(direct.billed_cost_usd));
  stripItem(root, 'Estimated cost', usd(agentCost),
    'API estimate · standard rates, short-context baseline' +
      (unpriced ? ' · partial: ' + unpriced + ' models with unknown pricing' : ''), !number(agentCost));
  if (full) {
    var pills = node('div', undefined, root); pills.className = 'pillrow';
    node('span', 'direct NordRouter ' + tokens(direct.tokens), pills).className = 'pill';
    var gatewayPill = node('span', 'other OpenClaw ' + tokens(gateway.tokens), pills);
    gatewayPill.className = 'pill';
    gatewayPill.title = 'OpenClaw tokens outside the NordRouter account';
    var hermesPill = node('span', 'other Hermes ' + tokens(hermes.tokens), pills);
    hermesPill.className = 'pill';
    hermesPill.title = 'Hermes tokens outside the NordRouter account';
  }
}

/* ---------- history chart ---------- */
// Agent rollups share the model stack; NordRouter-route rows from either agent
// are proxy weights for the direct account buckets, never their own series.
var AGENT_SOURCES = { openclaw: true, hermes: true };
var lastHistoryData = null;
var historyScale = 'linear';
var chartedModelRows = new Set();
// Keep the active model set far apart; name-based assignments survive sorting and period changes.
var MODEL_COLORS = {
    'deepseek/deepseek-v4.1-flash:fjord': '#e658b4',
    'free-final-fallback': '#8c8c8c',
    'gpt-6-luna': '#5676ff',
    'gpt-6-sol': '#41b1ff',
    'gpt-6.1-sol': '#88ebe8',
    'z-ai/glm-5.3': '#2adf82',
    'grok-4.6': '#ff6b66',
    'grok-4.7': '#ff9a42',
    'gpt-6-astra': '#b088ff',
    'gpt-5.6-terra': '#d7fb6a',
    'z-ai/glm-5.3-flash': '#f5d189',
    'deepseek/deepseek-v4-flash-0731': '#b62efa',
    'deepseek/deepseek-v4.1-flash': '#fa2efa',
    'gpt-5.6-luna': '#9460fb',
    'gpt-5.6-sol': '#2597d0',
    'moonshotai/kimi-k3': '#eea0d4',
    'moonshotai/kimi-k2.6': '#fa2e72',
    'gemma4:12b': '#72fa2e',
    'qwen3.8:unc-rvn-iq2xxs': '#d07a25',
    'qwen3.8:ad-iq2-xs': '#d02541',
    'qwen/qwen3.8-max': '#97c7f7',
    'frob/ministral-3:14b-thinking-q4_k_m': '#b4d025',
    'gpt-oss:20b': '#25d0d0',
    'nemotron-3-nano:30b-a3b-q4_k_m': '#92fcb5',
    'google/gemini-3.6-flash': '#eeada0',
    'minimax/minimax-m3': '#d02597'
};
function modelColor(model) {
  var name = String(model || '').toLowerCase();
  if (Object.prototype.hasOwnProperty.call(MODEL_COLORS, name)) return MODEL_COLORS[name];
  // Chart keys drop the provider prefix and routing variant, so match the
  // assigned palette on the bare model name to keep colours stable.
  var bare = name.split(':')[0].split('/').pop();
  for (var key in MODEL_COLORS) {
    if (!Object.prototype.hasOwnProperty.call(MODEL_COLORS, key)) continue;
    if (key.split(':')[0].split('/').pop() === bare) return MODEL_COLORS[key];
  }
  // Unseen models retain a stable, saturated accent until deliberately assigned.
  var hash = 2166136261;
  for (var i = 0; i < name.length; i++) hash = Math.imul(hash ^ name.charCodeAt(i), 16777619);
  hash >>>= 0;
  return 'hsl(' + (hash % 360) + ' 82% ' + (56 + (Math.floor(hash / 360) % 3) * 6) + '%)';
}
var activeHistoryTooltip = null;
window.addEventListener('pointerdown', function (event) {
  if (activeHistoryTooltip && event.target !== activeHistoryTooltip.rect) activeHistoryTooltip.hide();
});
window.addEventListener('scroll', function () {
  if (activeHistoryTooltip) activeHistoryTooltip.hide();
});
var SVG_NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, parent) {
  var element = document.createElementNS(SVG_NS, tag);
  Object.keys(attrs || {}).forEach(function (key) { element.setAttribute(key, attrs[key]); });
  if (parent) parent.append(element);
  return element;
}
function renderHistory(payload) {
  lastHistoryData = payload;
  chartedModelRows = new Set();
  var root = $('history');
  if (activeHistoryTooltip) activeHistoryTooltip.hide();
  root.replaceChildren();
  var byDate = new Map();
  var categories = new Map();
  var nordByDate = new Map();
  (payload.models || []).forEach(function (model) {
    if (!AGENT_SOURCES[model.source]) return;
    var nord = String(model.provider).toLowerCase() === 'nordrouter';
    var key = String(model.model_key || model.model || 'unknown');
    if (!categories.has(key)) categories.set(key, { key: key, label: key, total: 0 });
    (model.daily || []).forEach(function (day) {
      if (!number(day.total) || day.total < 0) return;
      var dates = nord ? nordByDate : byDate;
      if (!dates.has(day.date)) dates.set(day.date, new Map());
      var buckets = dates.get(day.date);
      buckets.set(key, (buckets.get(key) || 0) + day.total);
    });
  });
  var directByDate = new Map();
  var nordAttributedByDate = new Map();
  ((payload.source_daily || {}).nordrouter || []).forEach(function (day) {
    if (!number(day.tokens) || day.tokens < 0) return;
    directByDate.set(day.date, (directByDate.get(day.date) || 0) + day.tokens);
  });
  var unattributedKey = 'direct-nordrouter-unattributed';
  directByDate.forEach(function (direct, date) {
    var proxy = nordByDate.get(date);
    if (!byDate.has(date)) byDate.set(date, new Map());
    var buckets = byDate.get(date);
    var proxyTotal = proxy ? Array.from(proxy.values()).reduce(function (sum, value) { return sum + value; }, 0) : 0;
    if (!proxyTotal) {
      if (direct > 0) {
        buckets.set(unattributedKey, direct);
        categories.set(unattributedKey, { key: unattributedKey, label: 'NordRouter (model attribution unavailable)', nord: true });
      }
      return;
    }
    var allocations = Array.from(proxy, function (entry) {
      var exact = direct * (entry[1] / proxyTotal);
      return { key: entry[0], amount: Math.floor(exact), fraction: exact - Math.floor(exact) };
    });
    var remainder = direct - allocations.reduce(function (sum, item) { return sum + item.amount; }, 0);
    allocations.sort(function (a, b) { return b.fraction - a.fraction || a.key.localeCompare(b.key); });
    for (var i = 0; i < remainder; i++) allocations[i].amount++;
    allocations.forEach(function (item) {
      buckets.set(item.key, (buckets.get(item.key) || 0) + item.amount);
      if (item.amount > 0) {
        if (!nordAttributedByDate.has(date)) nordAttributedByDate.set(date, new Set());
        nordAttributedByDate.get(date).add(item.key);
      }
    });
  });
  (payload.models || []).forEach(function (model) {
    if (!AGENT_SOURCES[model.source]) return;
    var nord = String(model.provider).toLowerCase() === 'nordrouter';
    var key = String(model.model_key || model.model || 'unknown');
    if ((model.daily || []).some(function (day) {
      return number(day.total) && day.total > 0 && (!nord ||
        (nordAttributedByDate.get(day.date) || new Set()).has(key));
    })) chartedModelRows.add(key);
  });
  byDate.forEach(function (buckets) {
    buckets.forEach(function (amount, key) {
      categories.get(key).total = (categories.get(key).total || 0) + amount;
    });
  });
  var series = Array.from(byDate, function (item) {
    return { date: item[0], buckets: item[1], total_tokens: Array.from(item[1].values()).reduce(function (sum, value) { return sum + value; }, 0) };
  })
    .sort(function (a, b) { return a.date.localeCompare(b.date); });
  var peak = series.reduce(function (max, day) { return Math.max(max, day.total_tokens); }, 0);
  if (!series.length || peak <= 0) {
    node('p', 'No historical usage data available.', root).className = 'empty';
    return;
  }
  var modelCategories = Array.from(categories.values()).filter(function (category) { return category.key !== unattributedKey && category.total > 0; })
    .sort(function (a, b) { return a.label.localeCompare(b.label); });
  modelCategories.forEach(function (category) {
    category.color = modelColor(category.key);
  });
  var unattributed = categories.get(unattributedKey);
  if (unattributed && unattributed.total > 0) unattributed.color = '#8491a3';
  var stackOrder = unattributed && unattributed.total > 0 ? [unattributed].concat(modelCategories) : modelCategories;
  var width = Math.max(320, root.clientWidth || 900);
  var height = root.clientWidth && root.clientWidth <= 640 ? 260 : 300;
  var padL = 44, padR = 8, padT = 10, padB = 22;
  var plotW = width - padL - padR, plotH = height - padT - padB;
  var balanced = historyScale === 'balanced';
  // Fourth-root *per model* lifts small segments; cumulative-log compressed
  // later segments according to stack position and obscured them.
  var visualWeight = function (value) { return balanced ? Math.pow(value, 0.25) : value; };
  var scaleMax = balanced ? series.reduce(function (max, day) {
    return Math.max(max, Array.from(day.buckets.values()).reduce(function (sum, value) { return sum + visualWeight(value); }, 0));
  }, 0) : peak;
  var svg = svgEl('svg', { class: 'chart', viewBox: '0 0 ' + width + ' ' + height, preserveAspectRatio: 'none', role: 'img',
    'aria-label': balanced ? 'Daily model usage; fourth-root visual weighting per model, not a token axis' :
      'Daily token usage stacked by model; linear token axis' }, root);
  for (var g = 0; g <= 2; g++) {
    var y = padT + (plotH * g) / 2;
    svgEl('line', { x1: padL, y1: y, x2: width - padR, y2: y, stroke: '#343434', 'stroke-width': 1 }, svg);
    var label = svgEl('text', { x: padL - 6, y: y + 3, fill: '#8a8a8a', 'font-size': 9, 'text-anchor': 'end' }, svg);
    var tick = balanced ? Math.round((1 - g / 2) * 100) + '%' : peak * (1 - g / 2);
    label.textContent = balanced ? tick : tokens(Math.round(tick));
  }
  var slot = plotW / series.length;
  var barW = Math.max(2, Math.min(22, slot * 0.6));
  var axisLabels = Math.min(6, Math.max(2, Math.floor(plotW / 90)));
  var labelEvery = Math.ceil(series.length / axisLabels);
  var tooltip = node('div', undefined, root);
  tooltip.className = 'history-tooltip';
  tooltip.setAttribute('role', 'tooltip');
  function hideTooltip() {
    tooltip.style.visibility = 'hidden';
    if (activeHistoryTooltip && activeHistoryTooltip.hide === hideTooltip) activeHistoryTooltip = null;
  }
  function showTooltip(rect, label, x, y, touch) {
    if (activeHistoryTooltip && activeHistoryTooltip.hide !== hideTooltip) activeHistoryTooltip.hide();
    tooltip.textContent = label;
    tooltip.style.visibility = 'visible';
    var viewportW = window.innerWidth || 360, viewportH = window.innerHeight || 640;
    var tipW = tooltip.offsetWidth || 220, tipH = tooltip.offsetHeight || 44;
    tooltip.style.left = Math.max(8, Math.min(x + 12, viewportW - tipW - 8)) + 'px';
    tooltip.style.top = Math.max(8, Math.min(y - tipH - 12 < 8 ? y + 12 : y - tipH - 12,
      viewportH - tipH - 8)) + 'px';
    activeHistoryTooltip = { rect: rect, hide: hideTooltip, touch: touch };
  }
  series.forEach(function (day, index) {
    var x = padL + slot * index + (slot - barW) / 2;
    var stacked = 0;
    stackOrder.forEach(function (category) {
      var amount = day.buckets.get(category.key) || 0;
      if (amount <= 0) return;
      var weight = visualWeight(amount);
      stacked += weight;
      var barH = plotH * weight / scaleMax;
      var rect = svgEl('rect', {
        x: x, y: padT + plotH * (1 - stacked / scaleMax), width: barW, height: barH,
        fill: category.color, tabindex: 0,
      }, svg);
      var label = category.label + ' · ' + tokens(amount) + ' tokens · day total ' + tokens(day.total_tokens);
      rect.setAttribute('aria-label', label);
      rect.addEventListener('pointerenter', function (event) {
        if (event.pointerType === 'mouse') showTooltip(rect, label, event.clientX, event.clientY, false);
      });
      rect.addEventListener('pointermove', function (event) {
        if (event.pointerType === 'mouse') showTooltip(rect, label, event.clientX, event.clientY, false);
      });
      rect.addEventListener('pointerleave', function (event) {
        if (event.pointerType === 'mouse' && activeHistoryTooltip &&
            activeHistoryTooltip.rect === rect && !activeHistoryTooltip.touch) hideTooltip();
      });
      var touchStart = null;
      var suppressTouchFocusUntil = 0;
      rect.addEventListener('pointerdown', function (event) {
        if (event.pointerType === 'touch') {
          touchStart = { id: event.pointerId, x: event.clientX, y: event.clientY };
          suppressTouchFocusUntil = Date.now() + 500;
        }
      });
      rect.addEventListener('pointercancel', function () { touchStart = null; });
      rect.addEventListener('pointerup', function (event) {
        if (event.pointerType !== 'touch' || !touchStart || touchStart.id !== event.pointerId) return;
        var moved = Math.hypot(event.clientX - touchStart.x, event.clientY - touchStart.y) > 10;
        touchStart = null;
        if (moved) return;
        if (activeHistoryTooltip && activeHistoryTooltip.rect === rect && activeHistoryTooltip.touch) hideTooltip();
        else showTooltip(rect, label, event.clientX, event.clientY, true);
      });
      rect.addEventListener('focus', function () {
        // Chromium focuses tabindex SVG rects after touch pointerup. That
        // synthetic focus must not replace the persistent touch state.
        if (Date.now() < suppressTouchFocusUntil) return;
        var box = rect.getBoundingClientRect();
        showTooltip(rect, label, box.left + box.width / 2, box.top, false);
      });
      rect.addEventListener('blur', function () {
        if (activeHistoryTooltip && activeHistoryTooltip.rect === rect && !activeHistoryTooltip.touch) hideTooltip();
      });
    });
    if (index === 0 || index === series.length - 1 || index % labelEvery === 0) {
      var last = index === series.length - 1;
      var xl = svgEl('text', { x: last ? width - padR : padL + slot * index + slot / 2,
        y: height - 6, fill: '#8a8a8a', 'font-size': 9, 'text-anchor': last ? 'end' : 'middle' }, svg);
      xl.textContent = dateOnly(day.date);
    }
  });
  node('div', (balanced ? 'visual weight relative to tallest day · raw token peak ' : 'daily tokens · peak ') + tokens(peak), root).className = 'chart-summary';
}

/* ---------- model table ---------- */
var modelSortColumns = ['source', 'model', 'input', 'output', 'cache_read', 'cache_write', 'total', 'usd'];
var modelSort = { key: 'total', direction: 'default' };
var lastModelsData = null;
/* One row per model: every spelling that bills as one model is summed here.
   Which agent reported what stays in the payload for later drill-down. */
function rowKey(row) { return String(row.model_key || row.model || 'unknown'); }
function emptyTotals() { return { input: 0, output: 0, cache_read: 0, cache_write: 0, total: 0 }; }
function addTotals(into, values) {
  Object.keys(into).forEach(function (field) {
    if (number(values && values[field])) into[field] += values[field];
  });
  return into;
}
var SOURCE_LABELS = { nordrouter: 'NordRouter', openclaw: 'OpenClaw', hermes: 'Hermes', omniroute: 'OmniRoute (router-wide)' };
function displayName(name) {
  // Same model, same label: drop the routing suffix (:fjord, :saga) and the
  // context suffix (-900k) but keep the vendor prefix the sources report.
  var raw = String(name || '');
  var cut = raw.indexOf('/');
  var head = cut < 0 ? '' : raw.slice(0, cut + 1);
  var tail = cut < 0 ? raw : raw.slice(cut + 1);
  return head + tail.split(':')[0].replace(/-\d+k$/, '');
}
function preferredName(rows) {
  // The most common spelling labels the row: a vendor prefix shared by most
  // reports reads better than a bare alias that happens to be shorter.
  var counts = new Map();
  rows.forEach(function (row) {
    if (!row.model) return;
    var name = row.bucket_comparison || row.source === 'omniroute' ? row.model : displayName(row.model);
    counts.set(name, (counts.get(name) || 0) + 1);
  });
  var names = Array.from(counts.keys());
  if (!names.length) return 'unknown';
  names.sort(function (a, b) {
    return (counts.get(b) - counts.get(a)) || (a.length - b.length) || a.localeCompare(b);
  });
  return names[0];
}
function money(value) {
  if (!number(value)) return '—';
  return '$' + (value >= 0.01 ? value.toFixed(2) : value.toFixed(4));
}
function effectiveCost(row) {
  // Costs are priced on the server from disjoint token categories. A partial
  // native rate must never price another source or a missing category.
  if (row.bucket_comparison) return null;
  return number(row.cost_usd) ? row.cost_usd : null;
}
function tableModels(data) {
  var groups = new Map();
  (data.models || []).forEach(function (row) {
    var key = rowKey(row);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });
  return Array.from(groups.entries()).map(function (entry) {
    var rows = entry[1];
    var direct = rows.filter(function (row) { return row.source === 'nordrouter'; });
    var agents = rows.filter(function (row) { return row.source !== 'nordrouter' && row.source !== 'omniroute'; });
    var observation = rows.find(function (row) { return row.source === 'omniroute'; });
    var bucket = !!observation || agents.some(function (row) { return row.bucket_comparison; });
    var billed = direct.length > 0;
    // NordRouter bills the route once, so its rows are the shown figure and the
    // agent rows become the crossed-out comparison; otherwise agents are canonical.
    var shown = billed ? direct : agents.length ? agents : observation ? [observation] : [];
    var totals = emptyTotals();
    var comparison = emptyTotals();
    var cost = null, costKind = null, incompletePrice = false, daily = [], providers = [];
    shown.forEach(function (row) {
      addTotals(totals, row.totals);
      var rowCost = effectiveCost(row);
      if (number(rowCost)) cost = (cost || 0) + rowCost;
      if (!number(rowCost) || row.cost_kind === 'partial') incompletePrice = true;
      if (!costKind && row.cost_kind) costKind = row.cost_kind;
      if (row.provider && providers.indexOf(row.provider) < 0) providers.push(row.provider);
      (row.daily || []).forEach(function (day) { daily.push(day); });
    });
    if (!billed && incompletePrice && number(cost)) costKind = 'partial';
    agents.forEach(function (row) { addTotals(comparison, row.totals); });
    if (bucket) {
      cost = observation ? observation.cost_usd : null;
      costKind = observation ? observation.cost_kind : 'unknown';
    }
    return {
      model: preferredName(rows), model_key: entry[0],
      source: billed ? 'nordrouter' : (shown[0] || {}).source,
      provider: billed ? 'nordrouter' : (providers[0] || 'unknown'),
      totals: totals, cost_usd: cost,
      cost_kind: billed ? 'billed' : (costKind || (number(cost) ? 'estimated' : null)),
      comparison_total: billed && comparison.total > 0 ? comparison.total : null,
      comparison_categories: billed && comparison.total > 0 ? comparison : null,
      bucket: bucket, bucket_usage: observation && observation.bucket_usage,
      token_scope: bucket ? (agents.length ? 'agent-reported' : 'router-wide') : null,
      observed_daily: observation ? observation.daily : [],
      contributors: rows.map(function (row) {
        var value = effectiveCost(row);
        return { source: row.source, provider: row.provider, model: row.model,
                 tokens: (row.totals && row.totals.total) || 0, cost_usd: value,
                 cost_kind: number(row.cost_usd) ? row.cost_kind : (number(value) ? 'derived' : null) };
      }).sort(function (a, b) {
        // NordRouter bills every agent, so its token count already contains these
        // lines: it can never be added to them. Keep it last, agents first.
        var aBilled = a.source === 'nordrouter', bBilled = b.source === 'nordrouter';
        if (aBilled !== bBilled) return aBilled ? 1 : -1;
        return b.tokens - a.tokens;
      }),
      daily: daily,
    };
  });
}
function modelSortValue(row, key) {
  if (key === 'source') return row.source === 'nordrouter' ? 'NordRouter' : row.provider || null;
  if (key === 'model') return row.model || null;
  var value = key === 'usd' ? row.cost_usd : row.totals && row.totals[key];
  return number(value) ? value : null;
}
function sortedModels(rows) {
  var key = modelSort.direction === 'default' ? 'total' : modelSort.key;
  var direction = modelSort.direction === 'default' ? 'descending' : modelSort.direction;
  return TableSort.sortRows(rows, direction, function (row) { return modelSortValue(row, key); });
}
function syncModelSortHeaders() {
  modelSortColumns.forEach(function (key) {
    var view = TableSort.headerView(modelSort, key, {
      defaultKey: 'total',
      defaultDirection: 'descending',
      resetTitle: 'Reset to total tokens descending'
    });
    $('model-sort-head-' + key).setAttribute('aria-sort', view.ariaSort);
    $('model-sort-mark-' + key).textContent = view.mark;
    $('model-sort-' + key).title = view.title;
  });
}
function renderModels(data) {
  lastModelsData = data;
  var meta = data.meta || {};
  $('model-stamp').textContent = 'snapshot ' +
    (meta.generated_at_utc ? time(meta.generated_at_utc) : serverTime(meta.generated_at) + ' (server time)') +
    (meta.start_date && meta.end_date ? ' · ' + dateOnly(meta.start_date) + ' → ' + dateOnly(meta.end_date) : '') +
    ' · OpenClaw ' + (meta.openclaw_status || 'unknown') +
    (meta.openclaw_status === 'cached' && meta.openclaw_cached_at_utc
      ? ' (saved ' + time(meta.openclaw_cached_at_utc) + ')' : '') +
    ' · Hermes ' + (meta.hermes_status || 'unknown') +
    ' · NordRouter ' + (meta.nordrouter_status || 'unknown') +
    ' · OmniRoute ' + (meta.omniroute_status || 'not_configured');
  var body = $('models');
  body.replaceChildren();
  var models = sortedModels(tableModels(data));
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
    if (row.bucket) tr.className = 'bucket-row';
    var srcCell = node('td', undefined, tr);
    node('span', row.source === 'nordrouter' ? 'NordRouter' : row.provider || 'unknown', srcCell).className = 'src';
    var modelCell = node('td', undefined, tr);
    var modelName = node('span', undefined, modelCell); modelName.className = 'model-name';
    if (chartedModelRows.has(row.model_key)) {
      var swatch = node('span', undefined, modelName); swatch.className = 'model-swatch';
      swatch.style.background = modelColor(row.model_key || row.model || 'unknown');
      swatch.setAttribute('aria-hidden', 'true');
    }
    node('span', row.model || '—', modelName);
    var daily = row.daily || [];
    var parts = row.contributors || [];
    if (!row.bucket && (daily.length || parts.length > 1)) {
      var detail = node('details', undefined, modelCell);
      node('summary', daily.length ? daily.length + ' daily rows' : parts.length + ' sources', detail);
      daily.forEach(function (day) {
        node('div', dateOnly(day.date) + ' · ' + tokens(day.total) + ' tokens', detail).className = 'src';
      });
      if (parts.length > 1) {
        parts.forEach(function (part) {
          // Who reported this model, with their own tokens and USD, so the summed
          // figure on the right can be checked line by line. The NordRouter figure
          // already includes every agent, so it carries no countable token number.
          var billed = part.source === 'nordrouter';
          var kind = part.cost_kind || (billed && number(part.cost_usd) ? 'billed' : '');
          var line = node('div', (SOURCE_LABELS[part.source] || part.source) + ' · ' + (part.model || '—') +
            ' · ' + (billed ? 'unknown' : tokens(part.tokens) + ' tok') +
            ' · ' + money(part.cost_usd) + (kind ? ' ' + kind : ''), detail);
          line.className = 'src part';
          if (billed) line.title = 'NordRouter bills every agent, so this figure already includes the agent lines';
        });
      }
    }
    var comparedTotals = row.comparison_categories || row.totals || {};
    ['input', 'output', 'cache_read', 'cache_write'].forEach(function (field) {
      var cell = node('td', tokens(comparedTotals[field]), tr);
      cell.className = 'num';
      if (row.comparison_categories) cell.title = 'Agent-reported comparison; not direct NordRouter token categories';
    });
    var total = row.totals && number(row.totals.total) ? row.totals.total : null;
    var tokCell = node('td', undefined, tr); tokCell.className = 'num';
    if (row.comparison_total) {
      var pair = node('span', undefined, tokCell); pair.className = 'token-compare';
      // One crossed-out figure: everything the agents report for this model.
      var old = node('s', tokens(row.comparison_total), pair);
      old.title = 'Agent-reported for this model (OpenClaw + Hermes): ' +
        row.comparison_total.toLocaleString() + ' tokens';
      var actual = node('span', tokens(total), pair);
      actual.className = 'direct';
      actual.title = number(total) ? 'Direct NordRouter reported: ' + total.toLocaleString() + ' tokens' : 'Direct NordRouter tokens unavailable';
    } else {
      node('span', tokens(total), tokCell);
    }
    if (total && top > 0) {
      var mini = node('div', undefined, tokCell); mini.className = 'bar-mini';
      node('i', undefined, mini).style.width = Math.max(2, Math.round((total / top) * 100)) + '%';
    }
    var costCell = node('td', number(row.cost_usd) ? '$' + row.cost_usd.toFixed(2) : '—', tr);
    costCell.className = 'num';
    if (row.bucket) {
      var usage = row.bucket_usage;
      node('span', usage ? 'router-wide ' + (row.cost_kind || 'unknown') : 'router usage unavailable', costCell).className = 'cost-kind';
      if (usage && !number(row.cost_usd) && number(usage.known_cost_usd))
        node('span', 'known ≥ ' + money(usage.known_cost_usd), costCell).className = 'cost-kind';
      if (usage && usage.retention_limited) node('span', 'retained history only', costCell).className = 'cost-kind';
    } else node('span', row.comparison_total && !number(row.cost_usd) ? 'no direct match'
      : row.cost_kind || 'estimated', costCell).className = 'cost-kind';
    if (row.bucket) renderBucketDetails(row, modelCell, body);
  });
}

function bucketDetailRow(body, totals, cost, kind) {
  var tr = node('tr', undefined, body);
  tr.className = 'bucket-detail-row';
  tr.hidden = true;
  var source = node('td', undefined, tr);
  var model = node('td', undefined, tr);
  ['input', 'output', 'cache_read', 'cache_write', 'total'].forEach(function (field) {
    node('td', tokens(totals[field]), tr).className = 'num';
  });
  var usdCell = node('td', money(cost), tr); usdCell.className = 'num';
  node('span', kind, usdCell).className = 'cost-kind';
  return { row: tr, source: source, model: model };
}
function routeLine(route, body) {
  var line = bucketDetailRow(body, route.totals, route.cost_usd,
    number(route.cost_usd) ? 'router-wide estimated' : 'unknown: ' + route.unknown_reason);
  node('span', route.nordrouter === true ? 'NordRouter' : route.provider, line.source).className = 'src';
  node('span', route.model, line.model).className = 'src';
  return line.row;
}
function servingModels(routes, date) {
  var grouped = new Map();
  routes.forEach(function (route) {
    var slices = date ? (route.daily || []).filter(function (day) { return day.date === date; }) : [route];
    slices.forEach(function (slice) {
      var key = JSON.stringify([route.provider, route.model, route.price_id, route.nordrouter]);
      var total = grouped.get(key);
      if (!total) {
        total = Object.assign({}, route, { totals: {}, cost_usd: 0 });
        grouped.set(key, total);
      }
      Object.keys(slice.totals).forEach(function (field) {
        total.totals[field] = (total.totals[field] || 0) + slice.totals[field];
      });
      if (!number(slice.cost_usd)) {
        total.cost_usd = null;
        total.unknown_reason = route.unknown_reason;
      } else if (number(total.cost_usd)) total.cost_usd += slice.cost_usd;
    });
  });
  return Array.from(grouped.values());
}
function renderBucketDetails(row, cell, body) {
  node('div', row.token_scope + ' tokens', cell).className = 'cost-kind';
  var usage = row.bucket_usage;
  if (!usage) {
    node('div', 'No retained router observation.', cell).className = 'src';
    return;
  }
  var detail = node('details', undefined, cell);
  var serving = servingModels(usage.routes);
  node('summary', serving.length + (serving.length === 1 ? ' serving model' : ' serving models'), detail)
    .setAttribute('aria-label', row.model + ': serving models');
  var children = [];
  function syncRows() {
    children.forEach(function (child) { child.row.hidden = !detail.open || (child.day && !child.day.open); });
  }
  detail.addEventListener('toggle', syncRows);
  serving.forEach(function (route) { children.push({ row: routeLine(route, body) }); });
  var dates = new Map();
  (row.daily || []).forEach(function (day) {
    var value = dates.get(day.date) || {}; value.agent = (value.agent || 0) + day.total; dates.set(day.date, value);
  });
  (row.observed_daily || []).forEach(function (day) { var value = dates.get(day.date) || {}; value.observed = day; dates.set(day.date, value); });
  Array.from(dates.keys()).sort().forEach(function (date) {
    var value = dates.get(date), day = value.observed;
    var line = bucketDetailRow(body, day || {}, day && day.cost_usd,
      day ? 'router-wide ' + (number(day.cost_usd) ? 'estimated' : 'unknown') : 'router observation unavailable');
    children.push({ row: line.row });
    var daily = node('details', undefined, line.model);
    node('summary', dateOnly(date) + (row.token_scope === 'agent-reported' ? ' · ' + tokens(value.agent) + ' agent tok' : ''), daily)
      .setAttribute('aria-label', row.model + ': ' + dateOnly(date) + ' serving models');
    daily.addEventListener('toggle', syncRows);
    servingModels(usage.routes, date).forEach(function (route) {
      var tr = routeLine(route, body);
      tr.classList.add('bucket-day-route');
      children.push({ row: tr, day: daily });
    });
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
var selectedDays = 7;
var loadVersion = 0;
var lastLoaded = null;
function setPeriodButtons() {
  [30, 7, 1].forEach(function (days) {
    var button = $('period-' + days);
    button.setAttribute('aria-pressed', String(days === selectedDays));
  });
}
function renderPeriod(models, quotas) {
  renderHero(models);
  renderQuotas(quotas, models);
  renderHistory(models);
  renderModels(models);
}
function partialModels(state) {
  var openclaw = state.parts.openclaw, nordrouter = state.parts.nordrouter, hermes = state.parts.hermes, router = state.parts.omniroute;
  var openSource = openclaw && openclaw.sources && openclaw.sources.openclaw || { complete: false };
  var hermesSource = hermes && hermes.sources && hermes.sources.hermes || { complete: false };
  var directSource = nordrouter && nordrouter.sources && nordrouter.sources.nordrouter || { complete: false };
  var agents = [openSource, hermesSource];
  var agentsComplete = agents.every(function (source) { return source.complete; });
  var merged = (openclaw ? openclaw.models : []).concat(hermes ? hermes.models : [])
    .concat(nordrouter ? nordrouter.models : []).concat(router ? router.models : []);
  return {
    meta: Object.assign({}, (nordrouter || openclaw || hermes || {}).meta || {}, {
      days: state.days,
      openclaw_status: openclaw ? openclaw.meta.openclaw_status : 'loading',
      openclaw_cached_at_utc: openclaw ? openclaw.meta.openclaw_cached_at_utc : null,
      openclaw_error: openclaw ? openclaw.meta.openclaw_error : null,
      hermes_status: hermes ? hermes.meta.hermes_status : 'loading',
      nordrouter_status: nordrouter ? nordrouter.meta.nordrouter_status : 'loading',
      omniroute_status: router ? router.meta.omniroute_status : 'loading',
    }),
    totals: { total: agentsComplete && directSource.complete
      ? agents.reduce(function (sum, source) { return sum + (source.tokens || 0); }, 0) + directSource.tokens
      : null },
    sources: { openclaw: openSource,
      hermes: hermesSource, nordrouter: directSource,
      omniroute: router && router.sources.omniroute,
      openclaw_nordrouter_comparison_tokens: openclaw && openclaw.sources.openclaw_nordrouter_comparison_tokens,
      hermes_nordrouter_comparison_tokens: hermes && hermes.sources.hermes_nordrouter_comparison_tokens },
    source_daily: { nordrouter: nordrouter && nordrouter.source_daily ? nordrouter.source_daily.nordrouter : [] },
    models: merged,
  };
}
/* Saved OpenClaw usage is real measured data, but it is not the live window:
   name the failure, the original successful fetch time, and the window it
   actually describes instead of presenting it as current. */
function savedOpenClawNotice(state) {
  var openclaw = state.parts.openclaw;
  var meta = openclaw && openclaw.meta;
  if (!meta || meta.openclaw_status !== 'cached') return null;
  var stamp = meta.openclaw_cached_at_utc ? new Date(meta.openclaw_cached_at_utc).getTime() : NaN;
  var age = relativeAge(stamp);
  return 'OpenClaw Gateway unavailable: ' + (meta.openclaw_error || 'live read failed') +
    '. Showing saved OpenClaw usage from ' + time(meta.openclaw_cached_at_utc) +
    (age ? ' (' + age + ')' : '') + ', the last successful fetch of this window; it is not live data.';
}
function renderAvailable(state) {
  var models = partialModels(state);
  if (state.parts.openclaw || state.parts.nordrouter || state.parts.hermes || state.parts.omniroute) {
    renderHero(models);
    renderHistory(models);
    renderModels(models);
  }
  if (state.quotas) renderQuotas(state.quotas, models, state.quotaPending);
  var messages = Object.keys(state.errors).map(function (source) {
    return source + ' unavailable: ' + state.errors[source] + '. Last displayed data retained where available.';
  });
  var notice = savedOpenClawNotice(state);
  if (notice) messages.push(notice);
  $('error').textContent = messages.join(' ');
}
function mergeQuotaParts(state) {
  var cached = state.quotaParts.all || { quotas: [] };
  var rows = (cached.quotas || []).slice();
  ['codex', 'grok', 'grok_bot'].forEach(function (source) {
    var part = state.quotaParts[source];
    if (!part) return;
    rows = rows.filter(function (row) { return row.provider !== source; }).concat(part.quotas || []);
  });
  var latest = state.quotaParts.all || state.quotaParts.codex || state.quotaParts.grok || state.quotaParts.grok_bot;
  state.quotas = Object.assign({}, latest || {}, { quotas: rows });
}
function loadSources(version, state, requested) {
  requested = requested || ['quotas', 'codex', 'grok', 'grok_bot', 'nordrouter', 'openclaw', 'hermes', 'omniroute'];
  requested.forEach(function (source) {
    var ticket = state.requests[source] = (state.requests[source] || 0) + 1;
    function current() { return version === loadVersion && ticket === state.requests[source]; }
    if (source === 'quotas' || source === 'codex' || source === 'grok' || source === 'grok_bot') {
      var quotaSource = source === 'quotas' ? 'all' : source;
      state.quotaPending[quotaSource] = true;
      json('/api/v1/quotas' + (source === 'quotas' ? '' : '?source=' + source)).then(function (quotas) {
        if (!current()) return;
        state.quotaParts[quotaSource] = quotas;
        delete state.quotaPending[quotaSource];
        mergeQuotaParts(state);
        delete state.errors[source];
        renderAvailable(state);
      }).catch(function (error) {
        if (!current()) return;
        delete state.quotaPending[quotaSource];
        state.errors[source] = error.message;
        renderAvailable(state);
      });
      return;
    }
    json('/api/v1/model-breakdown?days=' + state.days + '&daily=true&source=' + source)
      .then(function (models) {
        if (!current()) return;
        if (models.meta.days !== state.days) throw new Error('period mismatch');
        if (source === 'openclaw' && models.meta.openclaw_status === 'unavailable')
          throw new Error(models.meta.openclaw_error
            ? 'Gateway read failed: ' + models.meta.openclaw_error : 'Gateway usage unavailable');
        if (source === 'hermes' && models.meta.hermes_status === 'unavailable')
          throw new Error('Hermes usage unavailable');
        // Every model part must describe the same window before they are combined.
        ['openclaw', 'hermes', 'nordrouter', 'omniroute'].forEach(function (other) {
          if (other === source) return;
          var previous = state.parts[other];
          if (previous && (previous.meta.start_date !== models.meta.start_date ||
              previous.meta.end_date !== models.meta.end_date)) delete state.parts[other];
        });
        state.parts[source] = models;
        delete state.errors[source];
        renderAvailable(state);
      }).catch(function (error) {
        if (!current()) return;
        state.errors[source] = error.message;
        renderAvailable(state);
      });
  });
}
function reload(force, nextDays) {
  if (busy && !nextDays) return;
  var days = nextDays || selectedDays;
  var version = ++loadVersion;
  selectedDays = days;
  var state = lastLoaded && lastLoaded.days === days ? lastLoaded :
    { days: days, quotas: null, quotaParts: {}, quotaPending: {}, parts: {}, errors: {}, requests: {} };
  state.errors = {};
  lastLoaded = state;
  busy = !nextDays;
  var button = $('refresh');
  button.disabled = busy;
  if (busy) button.classList.add('busy'); else button.classList.remove('busy');
  setPeriodButtons();
  $('error').textContent = '';
  loadSources(version, state);
  if (nextDays) return;
  json('/api/v1/display/update', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason: force ? 'force_button' : 'auto_refresh', view: days + 'd', device_id: 'web_dashboard' }),
  }).then(function () {
    if (version === loadVersion) loadSources(version, state, ['quotas', 'nordrouter']);
  }).catch(function (error) {
    if (version !== loadVersion) return;
    state.errors.refresh = error.message;
    renderAvailable(state);
  }).finally(function () {
    if (version !== loadVersion) return;
    busy = false; button.disabled = false; button.classList.remove('busy');
    setPeriodButtons();
  });
}

$('refresh').addEventListener('click', function () { reload(true); });
$('history-scale').addEventListener('click', function () {
  historyScale = historyScale === 'balanced' ? 'linear' : 'balanced';
  $('history-scale').textContent = 'Scale: ' + historyScale;
  $('history-scale').setAttribute('aria-pressed', String(historyScale === 'balanced'));
  if (lastHistoryData) renderHistory(lastHistoryData);
});
modelSortColumns.forEach(function (key) {
  $('model-sort-' + key).addEventListener('click', function () {
    modelSort = TableSort.nextState(modelSort, key, 'total');
    syncModelSortHeaders();
    if (lastModelsData) renderModels(lastModelsData);
  });
});
syncModelSortHeaders();
['30', '7', '1'].forEach(function (days) {
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

DASHBOARD_HTML = DASHBOARD_HTML.replace('<script>', '<script>\n' + TABLE_SORT_JS, 1)
