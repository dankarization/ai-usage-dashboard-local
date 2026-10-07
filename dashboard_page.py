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
* Honest history: the daily chart is drawn only from real ``token_usage.json``
  buckets and shows an explicit empty state when every bucket is zero.
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
.pill{font-size:11px;color:var(--muted);border:1px solid var(--line);background:var(--raised);border-radius:var(--pill);padding:2px 8px;font-variant-numeric:tabular-nums}

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

.win{display:grid;grid-template-columns:104px minmax(0,1fr) auto;gap:var(--s3);align-items:center;padding:3px 0}
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
.bar-mini{height:3px;border-radius:var(--pill);background:#171717;overflow:hidden;margin-top:4px;max-width:180px;border:1px solid var(--line-soft)}
.bar-mini i{display:block;height:100%;background:var(--accent)}

/* ---------- misc ---------- */
#error{color:#f0a8a2;font-size:12px;margin:var(--s3) 0 0}
.empty{color:var(--faint);font-size:12px;font-style:italic;margin:6px 0}
.foot{margin-top:var(--s6);color:var(--faint);font-size:11px;text-align:center}

@media (max-width:640px){
  .wrap{padding:0 var(--s3)}
  .topbar-in{padding:8px var(--s3);gap:var(--s2)}
  h1{font-size:13px}
  .stamp{font-size:10px}
  .strip-item{flex:1 1 44%;padding:9px var(--s3);border-top:1px solid var(--line)}
  .strip-item:nth-child(-n+2){border-top:0}
  .strip-item:nth-child(odd){border-left:0}
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
    <button id="refresh" class="btn primary" type="button"><span class="pulse" aria-hidden="true"></span>Refresh</button>
  </div>
</header>

<main class="wrap">
  <p id="error" role="status" aria-live="polite"></p>

  <section aria-labelledby="h-hero">
    <div class="sec-head"><h2 id="h-hero">Overview</h2><p class="note">last 30 days · USD · times shown in your browser timezone</p></div>
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
        <div><h3>Top-5 models · 7d</h3><div id="week"></div></div>
      </div>
    </div>
  </section>

  <section aria-labelledby="h-history">
    <div class="sec-head"><h2 id="h-history">Usage history</h2><p class="note">daily tokens from local buckets · drawn only where real data exists</p></div>
    <div id="history" class="panel chart-wrap"></div>
  </section>

  <section aria-labelledby="h-models">
    <div class="sec-head"><h2 id="h-models">Model costs</h2><p class="note">reported costs only; — means unavailable, not zero</p></div>
    <div class="panel">
      <p id="model-stamp" class="note"></p>
      <div class="scroll">
        <table>
          <thead><tr><th>Source</th><th>Model</th><th class="num">Tokens</th><th class="num">Cost USD</th></tr></thead>
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
function time(value) {
  if (value === null || value === undefined || value === '') return '—';
  var date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
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
    return { text: 'snapshot ' + (fallback || 'unknown') + ' (server time)', stale: true };
  }
  var zone = '';
  try { zone = Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch (error) { zone = ''; }
  var age = relativeAge(ms);
  return {
    text: 'snapshot ' + new Date(ms).toLocaleString() + (zone ? ' · ' + zone : '') + (age ? ' · ' + age : ''),
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
  node('span', window.label || 'Quota', row).className = 'win-label';

  if (number(window.used_percentage)) {
    var track = node('div', undefined, row);
    track.className = 'track';
    track.setAttribute('role', 'progressbar');
    track.setAttribute('aria-valuemin', '0');
    track.setAttribute('aria-valuemax', '100');
    track.setAttribute('aria-valuenow', String(Math.round(window.used_percentage)));
    track.setAttribute('aria-label', (window.label || 'Quota') + ' used');
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
    var provider = row.provider || 'unknown';
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
  return Array.from(groups.values());
}

/* Quota section order the user asked for: Codex accounts first (account 1,
   then account 2, each showing configured or unconfigured state), then Grok,
   then any remaining providers. NordRouter renders in its own section. */
var QUOTA_ORDER = ['codex', 'grok', 'grok_bot'];
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

function renderQuotas(data) {
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
  [['Balance', 'balance_usd'], ['Spend today', 'spend_today_usd'], ['Spend 7d', 'spend_7d_usd'], ['Spend 30d', 'spend_30d_usd']]
    .forEach(function (pair) {
      var box = node('div', undefined, $('metrics')); box.className = 'nr-cell';
      node('div', pair[0], box).className = 'k';
      var value = node('div', usd(nr[pair[1]]), box);
      value.className = 'v' + (number(nr[pair[1]]) ? '' : ' unknown');
    });
  topModels('today', nr.top_models_today);
  topModels('week', nr.top_models_7d);

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
  var summary = payload.summary || {};
  var daily = payload.daily || [];
  var active = daily.filter(function (day) { return number(day.total_tokens) && day.total_tokens > 0; });
  var latest = active.length ? active[active.length - 1] : null;
  var root = $('hero');
  root.replaceChildren();

  stripItem(root, 'Tokens · 30d', number(summary.total_tokens) ? tokens(summary.total_tokens) : '—',
    active.length + ' active day' + (active.length === 1 ? '' : 's'), !number(summary.total_tokens));
  stripItem(root, 'Cost · 30d', usd(summary.total_cost_usd), 'reported spend + estimates', !number(summary.total_cost_usd));
  stripItem(root, 'Latest active day', latest ? latest.date : '—',
    latest ? tokens(latest.total_tokens) + ' · ' + usd(latest.cost_usd) : 'no usage recorded', !latest);
  stripItem(root, 'AI active time · 30d', number(summary.total_ai_hours) ? summary.total_ai_hours.toFixed(2) + ' h' : '—',
    'from local session timing', !number(summary.total_ai_hours));

  // Subscription list-price equivalent. Filled by renderEstimate once the model
  // breakdown arrives; stays explicitly unavailable without measured tokens.
  var est = node('div', undefined, root); est.className = 'strip-item'; est.id = 'est-tile';
  node('div', 'Subscription list-price equiv · 7d', est).className = 'k';
  var estValue = node('div', '—', est); estValue.className = 'v unknown'; estValue.id = 'est-value';
  var estSub = node('div', 'waiting for model data…', est); estSub.className = 's'; estSub.id = 'est-sub';
  renderEstimate();

  var cats = summary.categories || {};
  var pills = Object.keys(cats).filter(function (key) { return number(cats[key]) && cats[key] > 0; });
  if (pills.length) {
    var row = node('div', undefined, root); row.className = 'pillrow';
    pills.sort(function (a, b) { return cats[b] - cats[a]; }).forEach(function (key) {
      node('span', key + ' ' + tokens(cats[key]), row).className = 'pill';
    });
  }
}

/* ---------- subscription list-price equivalent ---------- */
/* Only subscription sources are counted: NordRouter cost_usd is actual billed
   spend, not an estimate, so it is excluded. Every other source reports an
   API list-price equivalent derived from measured tokens and published rates.
   Without measured subscription tokens the figure is unavailable, never 0. */
var lastModels = null;
function subscriptionEstimate(models) {
  var rows = models || [];
  var priced = rows.filter(function (row) {
    return row.source !== 'nordrouter' && number(row.cost_usd);
  });
  return {
    count: priced.length,
    total: priced.reduce(function (sum, row) { return sum + row.cost_usd; }, 0),
  };
}
function renderEstimate() {
  var value = $('est-value');
  var sub = $('est-sub');
  if (!value) return;
  if (!lastModels) { if (sub) sub.textContent = 'waiting for model data…'; return; }
  var estimate = subscriptionEstimate(lastModels);
  if (!estimate.count) {
    value.textContent = '—';
    value.className = 'v unknown';
    if (sub) sub.textContent = 'unavailable · no measured subscription token data';
    return;
  }
  value.textContent = usd(estimate.total);
  value.className = 'v';
  if (sub) sub.textContent = 'estimate from published list prices · ' + estimate.count + ' model' + (estimate.count === 1 ? '' : 's');
}

/* ---------- history chart ---------- */
var SVG_NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, parent) {
  var element = document.createElementNS(SVG_NS, tag);
  Object.keys(attrs || {}).forEach(function (key) { element.setAttribute(key, attrs[key]); });
  if (parent) parent.append(element);
  return element;
}
function renderHistory(payload) {
  var root = $('history');
  root.replaceChildren();
  var daily = payload.daily || [];
  var series = daily.filter(function (day) { return number(day.total_tokens); });
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
  series.forEach(function (day, index) {
    var ratio = day.total_tokens / peak;
    var barH = Math.max(ratio > 0 ? 1.5 : 0, plotH * ratio);
    var x = padL + slot * index + (slot - barW) / 2;
    var rect = svgEl('rect', {
      x: x, y: padT + plotH - barH, width: barW, height: barH, rx: 1.5, fill: '#7aa2f7', opacity: 0.9,
    }, svg);
    svgEl('title', {}, rect).textContent = day.date + ' · ' + day.total_tokens.toLocaleString() + ' tokens · ' + usd(day.cost_usd);
    if (index === 0 || index === series.length - 1 || index % Math.ceil(series.length / 6) === 0) {
      var xl = svgEl('text', { x: padL + slot * index + slot / 2, y: height - 6, fill: '#8a8a8a', 'font-size': 9, 'text-anchor': 'middle' }, svg);
      xl.textContent = day.date.slice(5);
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
function renderModels(data) {
  var meta = data.meta || {};
  $('model-stamp').textContent = 'snapshot ' + time(meta.generated_at_utc || meta.generated_at) +
    (meta.start_date && meta.end_date ? ' · ' + meta.start_date + ' → ' + meta.end_date : '');
  var body = $('models');
  body.replaceChildren();
  var models = (data.models || []).slice().sort(function (a, b) {
    return (b.cost_usd === null || b.cost_usd === undefined ? -1 : b.cost_usd) -
           (a.cost_usd === null || a.cost_usd === undefined ? -1 : a.cost_usd);
  });
  lastModels = models;
  renderEstimate();
  if (!models.length) {
    var emptyRow = node('tr', undefined, body);
    var emptyCell = node('td', 'No model data available.', emptyRow);
    emptyCell.colSpan = 4;
    return;
  }
  var top = models.reduce(function (max, row) {
    var total = row.totals && number(row.totals.total) ? row.totals.total : 0;
    return Math.max(max, total);
  }, 0);
  models.forEach(function (row) {
    var tr = node('tr', undefined, body);
    var srcCell = node('td', undefined, tr);
    node('span', row.source || '—', srcCell).className = 'src';
    node('td', row.model || '—', tr);
    var total = row.totals && number(row.totals.total) ? row.totals.total : null;
    var tokCell = node('td', tokens(total), tr); tokCell.className = 'num';
    if (total && top > 0) {
      var mini = node('div', undefined, tokCell); mini.className = 'bar-mini';
      node('i', undefined, mini).style.width = Math.max(2, Math.round((total / top) * 100)) + '%';
    }
    var costCell = node('td', usd(row.cost_usd), tr); costCell.className = 'num';
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
function reload(force) {
  if (busy) return;
  busy = true;
  var button = $('refresh');
  button.disabled = true; button.classList.add('busy');
  $('error').textContent = '';
  var errors = [];
  var prefix = json('/api/v1/display/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: force ? 'force_button' : 'auto_refresh', view: '7d', device_id: 'web_dashboard' }),
      }).catch(function (error) { errors.push('Refresh failed: ' + error.message + '.'); })

  prefix.then(function () {
    return Promise.all([
      json('/api/v1/quotas').then(renderQuotas).catch(function (error) {
        errors.push('Quotas: ' + error.message + '. Last displayed data retained.');
      }),
      json('/api/v1/model-breakdown?days=7&daily=false').then(renderModels).catch(function (error) {
        errors.push('Models: ' + error.message + '. Last displayed data retained.');
      }),
      json('/token_usage.json').then(function (payload) {
        renderHero(payload);
        renderHistory(payload);
      }).catch(function (error) {
        errors.push('History: ' + error.message + '. Last displayed data retained.');
      }),
    ]);
  }).then(function () {
    if (errors.length) $('error').textContent = errors.join(' ');
  }).finally(function () {
    busy = false; button.disabled = false; button.classList.remove('busy');
  });
}

$('refresh').addEventListener('click', function () { reload(true); });
window.addEventListener('resize', function () {
  json('/token_usage.json').then(renderHistory).catch(function () {});
});
reload(false);
setInterval(function () { reload(false); }, 5 * 60000);
</script>
</body>
</html>'''
