"""Self-contained local dashboard without external assets.

The page is a single HTML document served by ``local_display_service``. It keeps
three invariants:

* No external assets: no ``<script src=``, no ``<link>``, no ``innerHTML``.
* No invented data: every number comes from a live endpoint, and a window that
  has no percentage renders as "unavailable"/"not configured", never as 0%.
* Honest history: the daily chart is drawn only from real ``token_usage.json``
  buckets and shows an explicit empty state when every bucket is zero.
"""

DASHBOARD_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="dark">
<title>AI Usage Dashboard</title>
<style>
:root{
  --bg:#0b0f17; --panel:#131a26; --panel-2:#182131; --line:#26334a;
  --text:#e8eef8; --muted:#94a3b8; --faint:#64748b;
  --accent:#6ea8fe; --ok:#34d399; --warn:#fbbf24; --danger:#f87171; --neutral:#7c8ba1;
  --radius:14px; --shadow:0 1px 2px rgba(0,0,0,.4),0 8px 24px rgba(0,0,0,.22);
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{
  background:
    radial-gradient(1100px 520px at 12% -12%,rgba(110,168,254,.10),transparent 62%),
    radial-gradient(900px 460px at 100% 0%,rgba(52,211,153,.07),transparent 60%),
    var(--bg);
  color:var(--text);
  font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased;
  padding:0 0 56px;
}
.wrap{max-width:1180px;margin:0 auto;padding:0 18px}
a{color:var(--accent)}

/* ---------- top bar ---------- */
.topbar{
  position:sticky;top:0;z-index:20;
  backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);
  background:rgba(11,15,23,.82);border-bottom:1px solid var(--line);
}
.topbar-in{max-width:1180px;margin:0 auto;padding:13px 18px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:11px;min-width:0}
.dot{width:10px;height:10px;border-radius:50%;background:var(--ok);box-shadow:0 0 0 4px rgba(52,211,153,.15);flex:none}
h1{font-size:1.06rem;margin:0;letter-spacing:-.01em;font-weight:650;white-space:nowrap}
.sub{color:var(--faint);font-size:.8rem;margin:0;white-space:nowrap}
.spacer{flex:1 1 auto}
.btn{
  appearance:none;border:1px solid var(--line);background:var(--panel-2);color:var(--text);
  font:inherit;font-weight:600;font-size:.86rem;padding:9px 16px;border-radius:10px;cursor:pointer;
  display:inline-flex;align-items:center;gap:8px;transition:background .15s,border-color .15s,transform .06s;
}
.btn:hover{background:#1e293c;border-color:#33415c}
.btn:active{transform:translateY(1px)}
.btn[disabled]{opacity:.55;cursor:progress}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#08111f}
.btn.primary:hover{background:#8ab8ff;border-color:#8ab8ff}
.pulse{width:8px;height:8px;border-radius:50%;background:currentColor;opacity:.9}
.busy .pulse{animation:blink 1s infinite}
@keyframes blink{0%,100%{opacity:.25}50%{opacity:1}}
.stamp{color:var(--faint);font-size:.78rem;font-variant-numeric:tabular-nums}

/* ---------- layout ---------- */
section{margin-top:26px}
.sec-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:0 0 12px}
h2{font-size:.95rem;margin:0;font-weight:650;letter-spacing:.02em;text-transform:uppercase;color:#b9c6da}
h3{font-size:.95rem;margin:0;font-weight:600}
.note{color:var(--faint);font-size:.78rem;margin:0}
.grid{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.card{
  background:linear-gradient(180deg,var(--panel),var(--panel-2));
  border:1px solid var(--line);border-radius:var(--radius);padding:16px;box-shadow:var(--shadow);
}
.card-head{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.badge{
  font-size:.74rem;font-weight:700;letter-spacing:.03em;text-transform:uppercase;
  padding:4px 9px;border-radius:999px;border:1px solid var(--line);color:#cbd5e1;background:#1b2434;
}
.badge.p-codex{color:#a7c7ff;border-color:#2f4570;background:#16203a}
.badge.p-grok{color:#e2c6ff;border-color:#4a3670;background:#221a33}
.badge.p-glm{color:#a7f3d0;border-color:#2c5a4b;background:#14261f}
.badge.p-claude{color:#f5c9a8;border-color:#5c4530;background:#2a2018}
.badge.p-antigravity{color:#a8e5f5;border-color:#2c5560;background:#14242a}
.badge.p-cursor{color:#c9d4ff;border-color:#3a4470;background:#1a1f33}
.badge.p-ollama{color:#e6e6e6;border-color:#3d4653;background:#20252d}
.badge.p-nordrouter{color:#a7f3d0;border-color:#2c5a4b;background:#14261f}
.badge.account{font-family:var(--mono);text-transform:none;font-weight:600;color:var(--muted)}
.chip{
  margin-left:auto;font-size:.72rem;font-weight:700;padding:4px 9px;border-radius:999px;
  border:1px solid var(--line);color:var(--muted);background:#1a2130;white-space:nowrap;
}
.chip.ok{color:var(--ok);border-color:#245c46;background:#10241c}
.chip.warn,.chip.stale{color:var(--warn);border-color:#5c4a17;background:#241f10}
.chip.danger{color:var(--danger);border-color:#5c2a2a;background:#241313}
.chip.unknown,.chip.not_configured,.chip.unavailable{color:var(--neutral);border-color:#33415580;background:#1a2130}

/* ---------- stat tiles ---------- */
.stat{padding:15px 16px}
.stat .k{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.stat .v{font-size:1.5rem;font-weight:680;letter-spacing:-.02em;margin-top:5px;font-variant-numeric:tabular-nums}
.stat .s{color:var(--faint);font-size:.75rem;margin-top:3px}
.stat .v.unknown{color:var(--faint);font-weight:600}

/* ---------- quota windows ---------- */
.win{padding:11px 0;border-top:1px dashed var(--line)}
.win:first-of-type{border-top:0;padding-top:0}
.win-top{display:flex;align-items:baseline;justify-content:space-between;gap:10px}
.win-label{font-size:.85rem;font-weight:600;color:#d7e0ee}
.win-pct{font-size:.92rem;font-weight:700;font-variant-numeric:tabular-nums;color:var(--muted)}
.win-pct.ok{color:var(--ok)} .win-pct.warn{color:var(--warn)} .win-pct.danger{color:var(--danger)}
.track{position:relative;height:9px;border-radius:999px;background:#0e1622;border:1px solid #1f2a3d;overflow:hidden;margin:9px 0 7px}
.fill{height:100%;border-radius:999px;transition:width .5s ease}
.fill.ok{background:linear-gradient(90deg,#1f9d6b,#34d399)}
.fill.warn{background:linear-gradient(90deg,#b4831f,#fbbf24)}
.fill.danger{background:linear-gradient(90deg,#b53b3b,#f87171)}
.fill.unknown{background:repeating-linear-gradient(135deg,#243044 0 6px,#1a2333 6px 12px)}
.win-foot{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;color:var(--faint);font-size:.75rem;font-variant-numeric:tabular-nums}
.win-foot b{color:var(--muted);font-weight:600}
.unavailable{margin:2px 0 0;color:var(--faint);font-size:.8rem;font-style:italic}

/* ---------- chart ---------- */
.chart-card{padding:14px 12px 8px}
.chart-wrap{width:100%;overflow:hidden}
svg.chart{display:block;width:100%;height:210px}
.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:.76rem;padding:6px 6px 2px}
.legend span{display:inline-flex;align-items:center;gap:6px}
.swatch{width:10px;height:10px;border-radius:3px;display:inline-block}

/* ---------- table ---------- */
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:.86rem}
th,td{text-align:left;padding:9px 8px;border-bottom:1px solid #1e2836;overflow-wrap:anywhere;vertical-align:top}
th{color:var(--muted);font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;font-weight:700;white-space:nowrap}
tbody tr:last-child td{border-bottom:0}
tbody tr:hover{background:#151d2b}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.src{font-family:var(--mono);font-size:.78rem;color:var(--muted)}
.mono{font-family:var(--mono);font-size:.8rem}
.bar-mini{height:6px;border-radius:999px;background:#0e1622;border:1px solid #1f2a3d;overflow:hidden;margin-top:6px;max-width:220px}
.bar-mini i{display:block;height:100%;background:linear-gradient(90deg,#2f6fd0,#6ea8fe)}

/* ---------- misc ---------- */
ol.top{margin:0;padding-left:20px}
ol.top li{margin:6px 0;display:flex;justify-content:space-between;gap:10px}
ol.top .amt{color:var(--muted);font-variant-numeric:tabular-nums}
#error{color:#ffc9c2;font-size:.83rem;margin:10px 0 0}
.empty{color:var(--faint);font-size:.83rem;font-style:italic;margin:6px 0}
.pillrow{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
.pill{font-size:.72rem;color:var(--muted);border:1px solid var(--line);background:#161e2c;border-radius:999px;padding:3px 9px;font-variant-numeric:tabular-nums}
.foot{margin-top:30px;color:var(--faint);font-size:.76rem;text-align:center}

@media (max-width:640px){
  .wrap{padding:0 13px}
  .topbar-in{padding:11px 13px;gap:10px}
  h1{font-size:.98rem} .sub{display:none}
  .grid{grid-template-columns:1fr;gap:12px}
  .stat .v{font-size:1.35rem}
  svg.chart{height:170px}
  body{padding-bottom:40px}
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
      <p class="sub">local · read-only</p>
    </div>
    <div class="spacer"></div>
    <span id="stamp" class="stamp" aria-live="polite">Loading…</span>
    <button id="refresh" class="btn primary" type="button"><span class="pulse" aria-hidden="true"></span>Refresh</button>
  </div>
</header>

<main class="wrap">
  <p id="error" role="status" aria-live="polite"></p>

  <section aria-labelledby="h-hero">
    <div class="sec-head"><h2 id="h-hero">Overview</h2><p class="note">Last 30 days · amounts in USD · times in your browser timezone</p></div>
    <div id="hero" class="grid"></div>
  </section>

  <section aria-labelledby="h-quota">
    <div class="sec-head"><h2 id="h-quota">Quotas</h2><p class="note">Color shows how much of each window is used · unavailable is not the same as 0%</p></div>
    <div id="quotas" class="grid"></div>
  </section>

  <section aria-labelledby="h-history">
    <div class="sec-head"><h2 id="h-history">Usage history</h2><p class="note">Daily tokens from local buckets · drawn only where real data exists</p></div>
    <div id="history" class="card chart-card"></div>
  </section>

  <section aria-labelledby="h-nord">
    <div class="sec-head"><h2 id="h-nord">NordRouter</h2><p id="nr-status" class="note"></p></div>
    <div id="metrics" class="grid"></div>
    <div class="grid" style="margin-top:14px">
      <div class="card"><h3>Top-5 models today</h3><div id="today"></div></div>
      <div class="card"><h3>Top-5 models · 7d</h3><div id="week"></div></div>
    </div>
  </section>

  <section aria-labelledby="h-codex">
    <div class="sec-head"><h2 id="h-codex">Codex</h2><p class="note">ChatGPT plan windows per profile</p></div>
    <div id="codex" class="grid"></div>
  </section>

  <section aria-labelledby="h-models">
    <div class="sec-head"><h2 id="h-models">Model costs</h2><p class="note">Reported costs only; — means unavailable, not zero</p></div>
    <div class="card">
      <p id="model-stamp" class="note"></p>
      <div class="scroll">
        <table>
          <thead><tr><th>Source</th><th>Model</th><th class="num">Tokens</th><th class="num">Cost USD</th></tr></thead>
          <tbody id="models"></tbody>
        </table>
      </div>
    </div>
  </section>

  <p class="foot">Auto-refresh every 60s · data never leaves this machine</p>
</main>

<script>
'use strict';
var $ = function (id) { return document.getElementById(id); };
var number = function (value) { return typeof value === 'number' && Number.isFinite(value); };

function usd(value) {
  if (!number(value)) return '—';
  var digits = Math.abs(value) >= 100 ? 2 : (Math.abs(value) >= 1 ? 2 : 4);
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
function providerName(provider) {
  var key = String(provider || 'unknown');
  var known = { codex: 'Codex', grok: 'Grok', glm: 'GLM', ollama: 'Ollama', claude: 'Claude',
                antigravity: 'Antigravity', cursor: 'Cursor', nordrouter: 'NordRouter' };
  if (known[key]) return known[key];
  return key.charAt(0).toUpperCase() + key.slice(1);
}
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

/* ---------- quota windows ---------- */
function windowRow(card, window) {
  var used = window.used_percentage;
  var row = node('div', undefined, card); row.className = 'win';
  var top = node('div', undefined, row); top.className = 'win-top';
  node('span', window.label || 'Quota', top).className = 'win-label';
  var pct = node('span', undefined, top);
  if (number(window.used_percentage)) {
    pct.textContent = window.used_percentage + '% used';
    pct.className = 'win-pct ' + severity(window.used_percentage);
  } else {
    pct.textContent = 'Used: —';
    pct.className = 'win-pct';
  }

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
  } else {
    var empty = node('div', undefined, row); empty.className = 'track';
    node('div', undefined, empty).className = 'fill unknown';
    node('p', 'No percentage reported for this window.', row).className = 'unavailable';
  }

  var foot = node('div', undefined, row); foot.className = 'win-foot';
  var resetMs = window.next_reset_time_ms;
  var resetIso = window.next_reset_iso;
  var when = countdown(resetMs);
  var resetText = 'Reset: ' + (resetMs || resetIso ? time(resetIso || resetMs) : '—');
  if (when) resetText += ' (' + when + ')';
  node('span', resetText, foot);
  if (window.remaining !== null && window.remaining !== undefined) {
    node('span', 'Remaining: ' + window.remaining, foot);
  } else if (number(window.remaining_percentage)) {
    node('span', window.remaining_percentage + '% remaining', foot);
  }
}

function quotaCard(group, root) {
  var card = node('div', undefined, root); card.className = 'card';
  var head = node('div', undefined, card); head.className = 'card-head';
  node('span', providerName(group.provider), head).className = 'badge p-' + String(group.provider || 'unknown').toLowerCase();
  if (group.account) node('span', group.account, head).className = 'badge account';
  var status = effectiveStatus(group);
  var chip = node('span', statusText(status), head);
  chip.className = 'chip ' + statusClass(status);
  group.windows.forEach(function (window) { windowRow(card, window); });
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
      groups.set(key, { provider: provider, account: account, status: row.status, windows: [] });
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

function renderQuotas(data) {
  var rows = data.quotas || [];
  var root = $('quotas');
  root.replaceChildren();

  var codex = rows.filter(function (row) { return row.provider === 'codex'; });
  var codexRoot = $('codex');
  codexRoot.replaceChildren();
  if (codex.length) {
    groupQuotas(codex).forEach(function (group) { quotaCard(group, codexRoot); });
  } else {
    node('p', 'No Codex snapshot available.', codexRoot).className = 'empty';
  }

  var others = rows.filter(function (row) { return row.provider !== 'nordrouter' && row.provider !== 'codex'; });
  if (!others.length) {
    node('p', 'No other quota snapshots available.', root).className = 'empty';
  } else {
    groupQuotas(others).forEach(function (group) { quotaCard(group, root); });
  }

  var nr = rows.find(function (row) { return row.provider === 'nordrouter'; }) || {};
  var nrStatus = $('nr-status');
  var text = nr.status ? 'Status: ' + statusText(nr.status) : 'No snapshot available — press Refresh';
  if (nr.today_complete === false) text += ' · Today model list incomplete; spend may use a fallback';
  if (nr.today_basis) text += ' · ' + nr.today_basis;
  nrStatus.textContent = text;

  $('metrics').replaceChildren();
  [['Balance USD', 'balance_usd'], ['Spend today', 'spend_today_usd'], ['Spend 7d', 'spend_7d_usd'], ['Spend 30d', 'spend_30d_usd']]
    .forEach(function (pair) {
      var box = node('div', undefined, $('metrics')); box.className = 'card stat';
      node('div', pair[0], box).className = 'k';
      var value = node('div', usd(nr[pair[1]]), box);
      value.className = 'v' + (number(nr[pair[1]]) ? '' : ' unknown');
    });
  topModels('today', nr.top_models_today);
  topModels('week', nr.top_models_7d);
  return rows;
}

function topModels(id, rows) {
  var root = $(id); root.replaceChildren();
  if (!rows || !rows.length) { node('p', 'No model data available.', root).className = 'empty'; return; }
  var list = node('ol', undefined, root); list.className = 'top';
  rows.slice().sort(function (a, b) { return (b.amount_usd || 0) - (a.amount_usd || 0); }).slice(0, 5)
    .forEach(function (row) {
      var item = node('li', undefined, list);
      node('span', row.id || 'Unknown', item);
      node('span', usd(row.amount_usd) + ' · ' + tokens(row.tokens) + ' tok', item).className = 'amt';
    });
}

/* ---------- overview + history ---------- */
function renderHero(payload) {
  var summary = payload.summary || {};
  var daily = payload.daily || [];
  var active = daily.filter(function (day) { return number(day.total_tokens) && day.total_tokens > 0; });
  var latest = active.length ? active[active.length - 1] : null;
  var root = $('hero');
  root.replaceChildren();
  var tiles = [
    ['Total tokens · 30d', number(summary.total_tokens) ? tokens(summary.total_tokens) : '—',
      active.length + ' active day' + (active.length === 1 ? '' : 's')],
    ['Total cost · 30d', usd(summary.total_cost_usd),
      'reported + estimated'],
    ['Latest active day', latest ? latest.date : '—',
      latest ? tokens(latest.total_tokens) + ' tokens · ' + usd(latest.cost_usd) : 'no usage recorded'],
    ['AI active time · 30d', number(summary.total_ai_hours) ? summary.total_ai_hours.toFixed(2) + ' h' : '—',
      'from local session timing'],
  ];
  tiles.forEach(function (tile) {
    var box = node('div', undefined, root); box.className = 'card stat';
    node('div', tile[0], box).className = 'k';
    var value = node('div', tile[1], box); value.className = 'v';
    node('div', tile[2], box).className = 's';
  });

  var cats = summary.categories || {};
  var pills = Object.keys(cats).filter(function (key) { return number(cats[key]) && cats[key] > 0; });
  if (pills.length) {
    var row = node('div', undefined, root); row.className = 'card stat';
    node('div', 'Tokens by source · 30d', row).className = 'k';
    var holder = node('div', undefined, row); holder.className = 'pillrow';
    pills.sort(function (a, b) { return cats[b] - cats[a]; }).forEach(function (key) {
      node('span', key + ' ' + tokens(cats[key]), holder).className = 'pill';
    });
  }
}

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
  var height = 210, padL = 56, padR = 12, padT = 14, padB = 26;
  var plotW = width - padL - padR, plotH = height - padT - padB;
  var svg = svgEl('svg', { class: 'chart', viewBox: '0 0 ' + width + ' ' + height, preserveAspectRatio: 'none', role: 'img' }, root);
  svgEl('title', {}, svg).textContent = 'Daily token usage for the last ' + series.length + ' days';
  for (var g = 0; g <= 4; g++) {
    var y = padT + (plotH * g) / 4;
    svgEl('line', { x1: padL, y1: y, x2: width - padR, y2: y, stroke: '#1e2836', 'stroke-width': 1 }, svg);
    var label = svgEl('text', { x: padL - 8, y: y + 4, fill: '#64748b', 'font-size': 10, 'text-anchor': 'end' }, svg);
    label.textContent = tokens(peak * (1 - g / 4));
  }
  var slot = plotW / series.length;
  var barW = Math.max(2, Math.min(26, slot * 0.62));
  series.forEach(function (day, index) {
    var ratio = day.total_tokens / peak;
    var barH = Math.max(ratio > 0 ? 2 : 0, plotH * ratio);
    var x = padL + slot * index + (slot - barW) / 2;
    var rect = svgEl('rect', {
      x: x, y: padT + plotH - barH, width: barW, height: barH, rx: 2, fill: '#6ea8fe', opacity: 0.92,
    }, svg);
    svgEl('title', {}, rect).textContent = day.date + ' · ' + day.total_tokens.toLocaleString() + ' tokens · ' + usd(day.cost_usd);
    if (index === 0 || index === series.length - 1 || index % Math.ceil(series.length / 6) === 0) {
      var xl = svgEl('text', { x: padL + slot * index + slot / 2, y: height - 8, fill: '#64748b', 'font-size': 10, 'text-anchor': 'middle' }, svg);
      xl.textContent = day.date.slice(5);
    }
  });
  var legend = node('div', undefined, root); legend.className = 'legend';
  var item = node('span', undefined, legend);
  var swatch = node('span', undefined, item); swatch.className = 'swatch'; swatch.style.background = '#6ea8fe';
  node('span', 'Daily tokens · ' + series.length + ' days · peak ' + tokens(peak), item);
  var span = node('span', undefined, legend);
  node('span', 'Total ' + tokens(series.reduce(function (sum, day) { return sum + day.total_tokens; }, 0)) + ' tokens', span);
}

/* ---------- model table ---------- */
function renderModels(data) {
  var meta = data.meta || {};
  $('model-stamp').textContent = 'Model snapshot: ' + time(meta.generated_at) +
    (meta.start_date && meta.end_date ? ' · ' + meta.start_date + ' → ' + meta.end_date : '');
  var body = $('models');
  body.replaceChildren();
  var models = (data.models || []).slice().sort(function (a, b) {
    return (b.cost_usd === null || b.cost_usd === undefined ? -1 : b.cost_usd) -
           (a.cost_usd === null || a.cost_usd === undefined ? -1 : a.cost_usd);
  });
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
      return response.json();
    })
    .finally(function () { clearTimeout(timer); });
}

var busy = false;
var lastQuotas = null;
function reload(force) {
  if (busy) return;
  busy = true;
  var button = $('refresh');
  button.disabled = true; button.classList.add('busy');
  $('error').textContent = '';
  var errors = [];
  var prefix = force
    ? json('/api/v1/display/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason: 'force_button', view: '7d', device_id: 'web_dashboard' }),
      }).catch(function (error) { errors.push('Refresh failed: ' + error.message + '.'); })
    : Promise.resolve();

  prefix.then(function () {
    return Promise.all([
      json('/api/v1/quotas').then(function (data) {
        lastQuotas = data;
        renderQuotas(data);
        $('stamp').textContent = 'Snapshot ' + time(data.generated_at);
      }).catch(function (error) {
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
  if (!lastQuotas) return;
  json('/token_usage.json').then(renderHistory).catch(function () {});
});
reload(false);
setInterval(function () { reload(false); }, 60000);
</script>
</body>
</html>'''
