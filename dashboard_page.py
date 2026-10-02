"""Self-contained local dashboard without external assets."""

DASHBOARD_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Usage Dashboard</title><style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#10141d;color:#e5eaf4}body{max-width:1100px;margin:auto;padding:24px}header{display:flex;align-items:center;justify-content:space-between;gap:16px}h1{font-size:1.6rem}h2{font-size:1.2rem}h3{font-size:1rem}section,.card{background:#1b2332;border:1px solid #344057;border-radius:12px;padding:18px;margin:16px 0}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:16px}.metric{font-size:1.5rem}.muted,small{color:#aebbd0}button{background:#85b8ff;color:#10141d;border:0;border-radius:8px;padding:10px 18px;cursor:pointer}button:disabled{opacity:.6;cursor:wait}table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{text-align:left;padding:9px 6px;border-bottom:1px solid #344057;overflow-wrap:anywhere}th:last-child,td:last-child{text-align:right}.scroll{overflow-x:auto}progress{width:100%;accent-color:#85b8ff}#error{color:#ffb4ab}li{margin:8px 0}p{overflow-wrap:anywhere}
</style></head><body>
<header><h1>AI Usage Dashboard</h1><button id="refresh" type="button">Refresh</button></header>
<p class="muted">Auto-refresh every 60s · amounts in USD · reset times in your browser timezone</p>
<p id="stamp" class="muted">Loading snapshot…</p><p id="error" role="status" aria-live="polite"></p>
<section><h2>NordRouter</h2><p id="nr-status" class="muted"></p><div id="metrics" class="grid"></div><div class="grid"><div><h3>Top-5 models today</h3><div id="today"></div></div><div><h3>Top-5 models · 7d</h3><div id="week"></div></div></div></section>
<section><h2>Codex quotas · 5h / weekly</h2><div id="codex" class="grid"></div></section>
<section><h2>Model costs · 7d</h2><p class="muted">Reported costs only; — means unavailable, not zero.</p><p id="model-stamp" class="muted"></p><div class="scroll"><table><thead><tr><th>Source</th><th>Model</th><th>Cost USD</th></tr></thead><tbody id="models"></tbody></table></div></section>
<script>
'use strict';
const $ = id => document.getElementById(id);
const number = value => typeof value === 'number' && Number.isFinite(value);
const usd = value => number(value) ? '$' + value.toFixed(6) : '—';
function node(tag, text, parent) {
 const element = document.createElement(tag);
 if (text !== undefined) element.textContent = text;
 if (parent) parent.append(element);
 return element;
}
function time(value) {
 if (value === null || value === undefined || value === '') return '—';
 const date = new Date(value);
 return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
}
function topModels(id, rows) {
 const root = $(id); root.replaceChildren();
 if (!rows?.length) { node('p', 'No model data available', root); return; }
 const list = node('ol', undefined, root);
 [...rows].sort((a,b) => (b.amount_usd ?? 0) - (a.amount_usd ?? 0)).slice(0,5)
  .forEach(row => node('li', `${row.id ?? 'Unknown'} — ${usd(row.amount_usd)}`, list));
}
function renderQuotas(data) {
 $('stamp').textContent = 'Quota snapshot: ' + time(data.generated_at);
 const rows = data.quotas ?? [];
 const nr = rows.find(row => row.provider === 'nordrouter') ?? {};
 $('nr-status').textContent = (nr.status ?? 'No snapshot available — press Refresh') +
  (nr.today_complete === false ? ' · Today model list incomplete; spend may use a fallback' : '') +
  (nr.today_basis ? ' · ' + nr.today_basis : '');
 $('metrics').replaceChildren();
 [['Balance USD','balance_usd'],['Spend today','spend_today_usd'],['Spend 7d','spend_7d_usd'],['Spend 30d','spend_30d_usd']].forEach(([label,key]) => {
  const box = node('div', undefined, $('metrics')); node('small', label, box);
  node('p', usd(nr[key]), box).className = 'metric';
 });
 topModels('today', nr.top_models_today); topModels('week', nr.top_models_7d);
 $('codex').replaceChildren();
 const profiles = new Map();
 rows.filter(row => row.provider === 'codex').forEach(row => {
  const key = row.account ?? 'codex';
  if (!profiles.has(key)) profiles.set(key, []);
  profiles.get(key).push(row);
 });
 if (!profiles.size) node('p', 'No Codex snapshot available', $('codex'));
 profiles.forEach((windows, account) => {
  const card = node('div', undefined, $('codex')); card.className = 'card';
  node('h3', account, card);
  windows.forEach(window => {
   node('p', window.label ?? 'Quota', card);
   node('p', number(window.used_percentage) ? `${window.used_percentage}% used` : 'Used: —', card);
   if (number(window.used_percentage)) {
    const bar = node('progress', undefined, card); bar.max = 100;
    bar.value = Math.max(0, Math.min(100, window.used_percentage));
    bar.setAttribute('aria-label', window.label ?? 'Quota used');
   }
   node('p', 'Reset: ' + time(window.next_reset_time_ms ?? window.next_reset_iso), card);
   if (window.status) node('small', window.status, card);
  });
 });
}
function renderModels(data) {
 $('model-stamp').textContent = 'Model snapshot: ' + time(data.meta?.generated_at);
 $('models').replaceChildren();
 [...(data.models ?? [])].sort((a,b) => (b.cost_usd ?? -1) - (a.cost_usd ?? -1)).forEach(row => {
  const tr = node('tr', undefined, $('models'));
  [row.source ?? '—', row.model ?? '—', usd(row.cost_usd)].forEach(value => node('td', value, tr));
 });
 if (!data.models?.length) { const td = node('td', 'No model data available', node('tr', undefined, $('models'))); td.colSpan = 3; }
}
async function json(url, options = {}) {
 const controller = new AbortController();
 const timer = setTimeout(() => controller.abort(), 180000);
 try {
  const response = await fetch(url, {...options, cache:'no-store', signal:controller.signal});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return await response.json();
 } finally { clearTimeout(timer); }
}
let busy = false;
async function reload(force = false) {
 if (busy) return;
 busy = true; $('refresh').disabled = true; $('error').textContent = '';
 const errors = [];
 try {
  if (force) {
   try { await json('/api/v1/display/update', {method:'POST', headers:{'Content-Type':'application/json'},
    body:JSON.stringify({reason:'force_button', view:'7d', device_id:'web_dashboard'})}); }
   catch (error) { errors.push('Refresh failed: ' + error.message); }
  }
  await Promise.all([
   ['/api/v1/quotas', renderQuotas, 'Quotas'],
   ['/api/v1/model-breakdown?days=7&daily=false', renderModels, 'Models']
  ].map(async ([url, render, label]) => {
   try { render(await json(url)); }
   catch (error) { errors.push(`${label}: ${error.message}. Last displayed data retained.`); }
  }));
 } finally { $('error').textContent = errors.join(' '); busy = false; $('refresh').disabled = false; }
}
$('refresh').addEventListener('click', () => reload(true));
reload(); setInterval(() => reload(), 60000);
</script></body></html>'''
