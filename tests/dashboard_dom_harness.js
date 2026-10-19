'use strict';
/*
 * Minimal DOM shim that runs the real dashboard script in Node and asserts the
 * rendering contract that matters for correctness:
 *
 *   - a window with no reported percentage is rendered as unavailable, never 0%
 *   - a configured window renders a colour-coded bar with the real percentage
 *   - high utilisation is colour-coded danger, mid is warn, low is ok
 *   - NordRouter is excluded from the quota grid but keeps its own metrics
 *   - empty history renders an explicit empty state instead of a fake chart
 *
 * Usage: node tests/dashboard_dom_harness.js <path-to-extracted-dashboard.js>
 */
const fs = require('fs');
const vm = require('vm');

const jsPath = process.argv[2];
if (!jsPath) {
  console.error('usage: node dashboard_dom_harness.js <dashboard.js>');
  process.exit(2);
}

function fail(message) {
  console.error('ASSERTION FAILED: ' + message);
  process.exit(1);
}
function assert(condition, message) {
  if (!condition) fail(message);
}
function assertEqual(actual, expected, message) {
  if (actual !== expected) {
    fail(message + ' (expected ' + JSON.stringify(expected) + ', got ' + JSON.stringify(actual) + ')');
  }
}

/* ---------------- DOM shim ---------------- */
class ClassList {
  constructor(element) { this.element = element; }
  add(...names) { names.forEach((n) => { if (!this.element._classes.includes(n)) this.element._classes.push(n); }); }
  remove(...names) { this.element._classes = this.element._classes.filter((c) => !names.includes(c)); }
  contains(name) { return this.element._classes.includes(name); }
}

class Element {
  constructor(tag, ns) {
    this.tagName = String(tag).toUpperCase();
    this.ns = ns || null;
    this.children = [];
    this.attrs = {};
    this.style = {};
    this._text = '';
    this._classes = [];
    this._id = null;
    this.classList = new ClassList(this);
    this.listeners = {};
  }
  set id(value) { this._id = value; if (value) registry.set(value, this); }
  get id() { return this._id; }
  set className(value) { this._classes = String(value || '').split(/\s+/).filter(Boolean); }
  get className() { return this._classes.join(' '); }
  set textContent(value) { this._text = value === undefined || value === null ? '' : String(value); this.children = []; }
  get textContent() { return this._text + this.children.map((c) => c.textContent).join(''); }
  setAttribute(key, value) { this.attrs[key] = String(value); }
  getAttribute(key) { return this.attrs[key]; }
  append(child) { this.children.push(child); return child; }
  replaceChildren(...kids) { this.children = kids.slice(); this._text = ''; }
  addEventListener(type, fn) { (this.listeners[type] = this.listeners[type] || []).push(fn); }
  get offsetWidth() { return 180; }
  get offsetHeight() { return 42; }
  getBoundingClientRect() { return { left: 20, top: 20, width: 20, height: 20 }; }
}

const registry = new Map();
['hero', 'quotas', 'history', 'history-scale', 'codex', 'metrics', 'today', 'week', 'selected-top-label',
 'model-stamp', 'models', 'stamp', 'error', 'refresh', 'nr-status',
 'period-30', 'period-7', 'period-1', 'overview-note', 'models-note'].forEach((id) => {
  registry.set(id, new Element('div'));
});
const sectionNotes = {
  'overview-note': 'Canonical direct NordRouter + agent usage, summed per model',
  'models-note': 'One row per model: spellings that bill as one model are summed. NordRouter rows: crossed-out agent total → direct tokens and billed USD. Input/output/cache on that row are agent comparisons, not direct token types. Model rows are not account totals.',
};
Object.entries(sectionNotes).forEach(([id, value]) => { registry.get(id).textContent = value; });
['source', 'model', 'input', 'output', 'cache_read', 'cache_write', 'total', 'usd'].forEach((key) => {
  ['model-sort-', 'model-sort-head-', 'model-sort-mark-'].forEach((prefix) => {
    registry.set(prefix + key, new Element(prefix === 'model-sort-head-' ? 'th' : 'span'));
  });
});

global.document = {
  getElementById: (id) => registry.get(id) || null,
  createElement: (tag) => new Element(tag),
  createElementNS: (ns, tag) => new Element(tag, ns),
};
const windowListeners = {};
global.window = { innerWidth: 390, innerHeight: 844,
  addEventListener: (type, fn) => { windowListeners[type] = fn; } };
let autoRefreshTick;
global.setInterval = (fn) => { autoRefreshTick = fn; return 0; };
const pendingFetches = [];
global.fetch = (url, options) => new Promise((resolve, reject) => {
  pendingFetches.push({ url, options, resolve, reject });
});

/* ---------------- run the real dashboard script ---------------- */
const code = fs.readFileSync(jsPath, 'utf8');
vm.runInThisContext(code, { filename: jsPath });
const initialSelectedDays = selectedDays;
const initialPressed = [7, 30].map((days) => registry.get('period-' + days).getAttribute('aria-pressed'));

['renderQuotas', 'renderHero', 'renderHistory', 'renderModels', 'tableModels', 'renderPeriod', 'severity', 'statusClass', 'providerName', 'tokens', 'usd', 'countdown', 'groupQuotas', 'effectiveStatus', 'brandMark', 'orderGroups', 'snapshotLabel', 'relativeAge', 'time', 'dateOnly', 'serverTime']
  .forEach((name) => assert(typeof global[name] === 'function' || typeof eval(name) === 'function',
    'dashboard script did not expose ' + name));

/* ---------------- fixtures (shapes copied from the live API) ---------------- */
const QUOTAS = {
  generated_at: '2026-10-03T03:37:51',
  quotas: [
    { account: 'codex_1', account_label: 'first@example.test', status: 'ok', provider: 'codex', label: '7d',
      used_percentage: 10, remaining_percentage: 90, next_reset_time_ms: 1791580259000,
      next_reset_iso: '2026-10-10T01:10:59', usage: null, remaining: null },
    { account: 'codex_2', account_label: 'second@example.test', status: 'not_configured', provider: 'codex', label: 'Codex Secondary',
      used_percentage: null, remaining_percentage: null, next_reset_time_ms: null,
      next_reset_iso: null, usage: null, remaining: null },
    { provider: 'grok', label: 'Weekly', used_percentage: 100, remaining_percentage: 0,
      next_reset_time_ms: 1791064244000, next_reset_iso: '2026-10-04T01:50:44', usage: null, remaining: null,
      product_usage: [{ product: 2, label: 'Grok Build', usage_percent: 100 }] },
    { provider: 'grok_bot', label: 'Weekly Grok Bot Limit', status: 'ok',
      used_percentage: 100, remaining_percentage: 0, next_reset_time_ms: 1791537783029 },
    { status: 'ok', balance_usd: 17.075988, spend_today_usd: 0.0, spend_7d_usd: 15.006731,
      spend_30d_usd: 46.459125, provider: 'nordrouter', label: 'NordRouter USD',
      used_percentage: null, remaining_percentage: null,
      top_models_today: [{ id: 'z-ai/glm-5.3', tokens: 11735928, amount_usd: 0.9477 }],
      top_models_7d: [{ id: 'z-ai/glm-5.3', tokens: 61934392, amount_usd: 7.8224 }],
      today_complete: false, today_basis: 'server daily date' },
  ],
};

const PAYLOAD = {
  meta: { generated_at: '2026-10-03T03:37:51', start_date: '2026-09-04', end_date: '2026-10-03', days: 30 },
  summary: { total_tokens: 1029393251, total_ai_hours: 0.01, total_cost_usd: 46.46,
             categories: { nordrouter: 1029393251, cursor: 0, glm: 0 } },
  daily: [
    { date: '2026-09-04', total_tokens: 1498658, cost_usd: 0.59 },
    { date: '2026-09-05', total_tokens: 0, cost_usd: 0.0 },
    { date: '2026-09-06', total_tokens: 250000000, cost_usd: 8.8 },
  ],
};

const MODELS = {
  meta: { generated_at: '2026-10-03T03:49:55', start_date: '2026-09-04', end_date: '2026-10-03', days: 30 },
  totals: { total: 30 },
  sources: { nordrouter: { tokens: 20, billed_cost_usd: 0.4, complete: true },
    openclaw: { tokens: 10, estimated_cost_usd: 0.2, complete: true },
    openclaw_nordrouter_comparison_tokens: 21,
    hermes: { tokens: 6, estimated_cost_usd: 0.1, unpriced_models: 0, complete: true },
    hermes_nordrouter_comparison_tokens: 5 },
  source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 20 }] },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'z-ai/glm-5.3', cost_usd: 0.4, totals: { total: 19 }, daily: [] },
    { source: 'openclaw', provider: 'nordrouter', model: 'nr-route', cost_usd: 0.5, totals: { total: 21 }, daily: [{ date: '2026-10-03', total: 21 }] },
    { source: 'openclaw', provider: 'xai', model: 'grok-test', cost_usd: 0.2, totals: { total: 10 }, daily: [{ date: '2026-10-03', total: 10 }] },
    { source: 'hermes', provider: 'nordrouter', model: 'nr-route', cost_usd: null, totals: { total: 5 }, daily: [{ date: '2026-10-03', total: 5 }] },
    { source: 'hermes', provider: 'openai', model: 'hermes-model', cost_usd: 0.1, cost_kind: 'estimated', totals: { total: 6 }, daily: [{ date: '2026-10-03', total: 6 }] },
  ],
};

/* ---------------- helpers ---------------- */
function walk(element, out) {
  out = out || [];
  out.push(element);
  element.children.forEach((child) => walk(child, out));
  return out;
}
function all(root) { return walk(root, []); }
function withRole(root, role) { return all(root).filter((e) => e.getAttribute('role') === role); }
function findByText(root, needle) { return all(root).filter((e) => e.textContent.includes(needle)); }
function cardFor(root, needle) {
  return all(root).filter((e) => e.classList.contains('limit-group') && e.textContent.includes(needle));
}
function segmentLabel(segment) { return segment.getAttribute('aria-label'); }
function tooltipText(root) { return all(root).find((e) => e.classList.contains('history-tooltip')).textContent; }
/* The route cell carries the provider plus the agent that reported the row. */
function routeName(row) {
  const src = all(row.children[0]).find((e) => e.classList.contains('src'));
  return src && src.textContent;
}
function modelRow(name, source) {
  return registry.get('models').children.find((row) => row.children[1].children[0].textContent === name &&
    (!source || routeName(row) === source));
}
function modelSwatch(name, source) {
  const row = modelRow(name, source);
  return row && all(row.children[1]).find((e) => e.classList.contains('model-swatch'));
}
function chartColor(name) {
  const segment = all(registry.get('history')).find((e) => e.tagName === 'RECT' &&
    segmentLabel(e).startsWith(name + ' · '));
  return segment && segment.getAttribute('fill');
}

/* ---------------- pure helpers ---------------- */
assertEqual(severity(10), 'ok', 'severity(10)');
assertEqual(severity(50), 'warn', 'severity(50)');
assertEqual(severity(100), 'danger', 'severity(100)');
assertEqual(statusClass('not_configured'), 'not_configured', 'statusClass(not_configured)');
assertEqual(statusClass('stale'), 'stale', 'statusClass(stale)');
assertEqual(providerName('codex'), 'Codex', 'providerName(codex)');
assertEqual(providerName('grok'), 'Grok', 'providerName(grok)');
assertEqual(brandMark('codex'), 'OI', 'brandMark(codex) is the Codex login mark');
assertEqual(brandMark('grok'), 'xAI', 'brandMark(grok) is the xAI mark');
assertEqual(brandMark('nordrouter'), 'NR', 'brandMark(nordrouter)');
assertEqual(tokens(1029393251), '1.03B', 'tokens(1.03B)');
assertEqual(tokens(61934392), '61.9M', 'tokens(61.9M)');
assertEqual(usd(null), '—', 'usd(null) is unknown, not $0');
assertEqual(usd(0), '$0.0000', 'usd(0) is a real zero');
assertEqual(countdown(null), null, 'countdown(null) has no countdown');
assert(/^in (6h 0m|5h 59m)$/.test(countdown(Date.now() + 6 * 3600000)),
  'countdown formats an hours-scale delta, got ' + countdown(Date.now() + 6 * 3600000));
assert(/^in (2d 3h|2d 2h)$/.test(countdown(Date.now() + 2 * 86400000 + 3 * 3600000)),
  'countdown formats a day-scale delta, got ' + countdown(Date.now() + 2 * 86400000 + 3 * 3600000));
assertEqual(countdown(Date.now() - 1000), 'resetting…', 'a past reset reads as resetting');
const originalZone = process.env.TZ;
process.env.TZ = 'Asia/Tbilisi';
assertEqual(time('2026-10-09T09:23:00Z'), '09.10.2026 13:23', 'reset instant uses local 24-hour time');
assertEqual(dateOnly('2026-09-06'), '06.09.2026', 'daily bucket uses day.month.year');
assertEqual(serverTime('2026-10-03T12:51:45'), '03.10.2026 12:51', 'legacy server wall time uses same convention');
process.env.TZ = 'America/New_York';
assertEqual(time('2026-10-09T09:23:00Z'), '09.10.2026 05:23', 'reset instant follows viewer timezone');
if (originalZone === undefined) delete process.env.TZ;
else process.env.TZ = originalZone;

/* ---------------- quota rendering ---------------- */
renderQuotas(QUOTAS, MODELS);

const codexRoot = registry.get('codex');
const quotaRoot = registry.get('quotas');

const codexRows = all(codexRoot).filter((e) => e.classList.contains('limit-group'));
assert(codexRows.length === 2, 'expected two Codex account rows, got ' + codexRows.length);
// Account order must follow the quota payload: codex_1 before codex_2.
assert(codexRows[0].textContent.includes('first@example.test'), 'first account display name must render');
assert(codexRows[1].textContent.includes('second@example.test'), 'second account display name must render');
assert(!codexRoot.textContent.includes('codex_1') && !codexRoot.textContent.includes('codex_2'),
  'internal account IDs must not be shown when display names are available');
// Each row carries a brand mark.
assertEqual(all(codexRows[0]).filter((e) => e.classList.contains('brandtile'))[0].textContent, 'OI',
  'Codex rows must carry the OI brand mark');
// A not-configured account is a subdued single row, not a large empty card.
assert(codexRows[1].classList.contains('subdued'),
  'an unconfigured account must render as a subdued row');

const configured = cardFor(codexRoot, 'first@example.test')[0];
assert(configured, 'codex_1 row missing');
const configuredBars = withRole(configured, 'progressbar');
assert(configuredBars.length === 1, 'configured codex window must render exactly one bar');
assertEqual(configuredBars[0].getAttribute('aria-valuenow'), '10', 'codex_1 aria-valuenow');
const configuredFill = all(configuredBars[0]).find((e) => e.classList.contains('fill'));
assert(configuredFill.classList.contains('ok'), 'codex_1 at 10% must be colour-coded ok');
assertEqual(configuredFill.style.width, '10%', 'codex_1 fill width');
assert(configured.textContent.includes('10% used'), 'codex_1 must show its real percentage');
assert(configured.textContent.includes('90% left'), 'codex_1 must show its remaining percentage');
assert(configured.textContent.includes('resets ' + time(1791580259000)), 'quota reset uses local date convention');

const unconfigured = cardFor(codexRoot, 'second@example.test')[0];
assert(unconfigured, 'codex_2 row missing');
assert(withRole(unconfigured, 'progressbar').length === 0,
  'an unconfigured window must not render a percentage bar');
assert(unconfigured.textContent.includes('Not configured'), 'codex_2 must be labelled Not configured');
assert(unconfigured.textContent.includes('Used: —'), 'codex_2 must show Used: — not 0%');
assert(!unconfigured.textContent.includes('0% used'), 'an unconfigured window must never claim 0% used');
assert(unconfigured.textContent.includes('No percentage reported'), 'codex_2 must explain the missing value');
assert(unconfigured.textContent.includes('No second Codex login on this machine yet.'),
  'an unconfigured account must say what is missing');

const grokCard = cardFor(quotaRoot, 'Grok')[0];
assert(grokCard, 'Grok card missing from the quota grid');
const grokBars = withRole(grokCard, 'progressbar');
assertEqual(grokBars.length, 2, 'Grok and Grok Bot must have separate bars in one provider group');
const grokFill = all(grokBars[0]).find((e) => e.classList.contains('fill'));
assert(grokFill.classList.contains('danger'), 'Grok at 100% must be colour-coded danger');
assertEqual(grokFill.style.width, '100%', 'Grok fill width');
assert(grokCard.textContent.includes('100% used'), 'Grok must show 100% used');
assert(grokCard.textContent.includes('Grok Build: 100%'), 'Grok must show the verified product name and percentage');
assert(!grokCard.textContent.includes('of shared credits (not tokens or separate limits)'),
  'the unwanted product suffix must not be displayed');
assert(grokCard.textContent.includes('0% left'), 'Grok must show 0% left');
// The live Grok row carries a real percentage but no explicit status field;
// it must read as Active, not as Unknown.
assert(grokCard.textContent.includes('Active'), 'a window with a real percentage must read as Active');
assert(!grokCard.textContent.includes('Unknown'), 'a live window must not be labelled Unknown');
assertEqual(all(grokCard).filter((e) => e.classList.contains('brandtile')).length, 1,
  'the Grok group must have one icon');
assertEqual(all(grokCard).filter((e) => e.classList.contains('pname')).map((e) => e.textContent).join(','), 'Grok',
  'the Grok group must have one provider title');
assertEqual(all(grokCard).filter((e) => e.classList.contains('chip')).length, 1,
  'the Grok group must have one status badge');
const grokWindows = all(grokCard).filter((e) => e.classList.contains('win'));
assertEqual(grokWindows.length, 2, 'Grok must contain two quota rows');
assertEqual(grokWindows.map((e) => e.getAttribute('data-provider')).join(','), 'grok,grok_bot',
  'each row must retain its independent API provider identity');
assertEqual(grokWindows.map((e) => all(e).find((child) => child.classList.contains('win-label')).textContent).join(','),
  'Weekly,Grok Bot', 'quota row labels must distinguish the weekly pool and Bot relay');
assertEqual(grokWindows.map((e) => withRole(e, 'progressbar').length).join(','), '1,1',
  'each Grok row must have one independent bar');
assert(grokWindows[0].textContent.includes('resets ' + time(1791064244000)),
  'weekly Grok reset must remain on its row');
assert(grokWindows[1].textContent.includes('resets ' + time(1791537783029)),
  'Grok Bot reset must remain on its row');
assert(!grokWindows[0].textContent.includes(time(1791537783029)),
  'weekly Grok row must not inherit the Bot reset');

function renderedGrok(total, products) {
  const root = new Element('div');
  windowRow(root, { provider: 'grok', label: 'Weekly', used_percentage: total,
    remaining_percentage: 100 - total, product_usage: products });
  const bar = withRole(root, 'progressbar')[0];
  const fill = all(bar).find((e) => e.classList.contains('fill'));
  return { root, bar, fill, segments: all(fill).filter((e) => e.classList.contains('product-segment')),
    entries: all(root).filter((e) => e.classList.contains('product-legend-item')) };
}
const liveGrok = renderedGrok(3, [
  { product: 2, label: 'Grok Build', usage_percent: 2 },
  { product: 4, label: 'Grok Chat', usage_percent: 1 },
]);
assertEqual(liveGrok.fill.style.width, '3%', 'live weekly pool uses only 3% of the whole track');
assertEqual(liveGrok.bar.getAttribute('aria-valuenow'), '3', 'weekly bar reports shared total');
assertEqual(liveGrok.segments.length, 2, 'live pool has two product segments');
assertEqual(liveGrok.segments.map((s) => s.style.width).join(','),
  '66.66666666666666%,33.33333333333333%', '2:1 split is inside the 3% fill');
assertEqual(liveGrok.entries.map((e) => e.textContent).join(','),
  'Grok Build: 2%,Grok Chat: 1%', 'legend retains API product names and percentages');
assertEqual(liveGrok.entries.map((e) => e.children[0].style.backgroundColor).join(','),
  liveGrok.segments.map((s) => s.style.backgroundColor).join(','),
  'legend swatches match the bar segment colors');
assert(liveGrok.segments[0].style.backgroundColor !== liveGrok.segments[1].style.backgroundColor,
  'products are visually distinguishable');
assert(!liveGrok.root.textContent.includes('shared credits'), 'unwanted suffix is absent');

for (const [total, level] of [[3, 'ok'], [60, 'warn'], [90, 'danger']]) {
  const amounts = Array.from({ length: 12 }, () => total / 12);
  const many = renderedGrok(total, amounts.map((amount, index) =>
    ({ product: index, label: 'Product ' + index, usage_percent: amount })));
  assertEqual(many.fill.style.width, total + '%', level + ' total width');
  assert(many.fill.classList.contains(level), level + ' follows overall severity');
  assertEqual(many.segments.length, 12, level + ' renders every product');
  assertEqual(many.entries.length, 12, level + ' legend covers every product');
  assertEqual(new Set(many.segments.map((s) => s.style.backgroundColor)).size, 12,
    level + ' products use distinct shades in the same severity family');
  assert(many.segments.every((s) => s.style.backgroundColor ===
    many.entries[many.segments.indexOf(s)].children[0].style.backgroundColor),
  level + ' swatches match the segments');
}
const tiny = renderedGrok(0.2, [
  { product: 2, label: '', usage_percent: 0.1 },
  { product: null, usage_percent: 0 },
  { product: 4, label: 'Chat', usage_percent: 0.1 },
]);
assertEqual(tiny.fill.style.width, '0.2%', 'tiny total remains tiny');
assertEqual(tiny.segments.length, 2, 'zero product has no visible width');
assertEqual(tiny.entries.length, 3, 'zero and unknown labels remain in the legend');
assert(tiny.root.textContent.includes('Product 2 (unknown): 0.1%') &&
  tiny.root.textContent.includes('Unknown product: 0%'), 'unknown labels are honest');
const zero = renderedGrok(0, [{ product: 2, label: 'Build', usage_percent: 0 }]);
assertEqual(zero.fill.style.width, '0%', 'real zero is not unavailable');
assertEqual(zero.segments.length, 0, 'real zero creates no visible segment');
assertEqual(zero.entries.length, 1, 'real zero retains its product label');
for (const breakdown of [null, [], [{ product: 2, usage_percent: 2 }],
  [{ product: 2, usage_percent: 4 }], [{ product: 2, usage_percent: NaN }]]) {
  const fallback = renderedGrok(3, breakdown);
  assertEqual(fallback.fill.style.width, '3%', 'invalid breakdown keeps only the reported total');
  assertEqual(fallback.segments.length, 0, 'invalid breakdown must not fabricate segments');
  assertEqual(fallback.entries.length, 0, 'invalid breakdown must not show a misleading legend');
}
assertEqual(effectiveStatus({ windows: [{ used_percentage: 42 }] }), 'ok',
  'effectiveStatus falls back to ok when a percentage exists');
assertEqual(effectiveStatus({ windows: [{ used_percentage: null }] }), 'unknown',
  'effectiveStatus stays unknown without a percentage or status');
assertEqual(effectiveStatus({ status: 'not_configured', windows: [{ used_percentage: 42 }] }), 'not_configured',
  'an explicit status wins over the percentage fallback');

assert(!quotaRoot.textContent.includes('NordRouter'), 'NordRouter must not appear in the quota grid');
assertEqual(all(quotaRoot).filter((e) => e.classList.contains('limit-group')).length, 1,
  'the quota list must hold one shared Grok provider group');
assert(grokWindows[1].textContent.includes('100% used'), 'Grok Bot must show its independent percentage');
const degradedGrok = groupQuotas([
  { provider: 'grok', label: 'Weekly', used_percentage: 42 },
  { provider: 'grok_bot', label: 'Weekly Grok Bot Limit', status: 'unavailable', used_percentage: null },
]);
assertEqual(degradedGrok.length, 1, 'a failed Bot read must remain in the Grok group');
assertEqual(effectiveStatus(degradedGrok[0]), 'ok', 'a failed Bot read must not hide a live weekly pool');
assertEqual(orderGroups([{ provider: 'glm', account: '' }, { provider: 'grok', account: '' }, { provider: 'codex', account: 'codex_2' }, { provider: 'codex', account: 'codex_1' }])
  .map((g) => g.provider + (g.account || '')).join(','),
  'codexcodex_1,codexcodex_2,grok,glm',
  'quota order must be Codex accounts, then Grok, then the rest');
assert(registry.get('nr-status').textContent.includes('incomplete'),
  'NordRouter partial-today note must be surfaced');
assert(registry.get('metrics').textContent.includes('$17.08'),
  'NordRouter balance must render as USD, got: ' + registry.get('metrics').textContent);
assert(registry.get('today').textContent.includes('z-ai/glm-5.3'), 'top models today must render');

/* ---------------- period, totals, history, and source tables ---------------- */
renderPeriod(MODELS, QUOTAS);
Object.entries(sectionNotes).forEach(([id, value]) => {
  assertEqual(registry.get(id).textContent, value, '30d ' + id + ' note stays static');
});
let heroText = registry.get('hero').textContent;
assert(registry.get('codex').textContent.includes('Weekly') &&
  !/(?:7|30)d/.test(registry.get('codex').textContent),
  'provider quota period abbreviations are not rendered outside selector buttons');
assertEqual(all(registry.get('hero')).filter((e) => e.classList.contains('strip-item'))
  .map((e) => e.children[0].textContent).join(','),
  'Total tokens,NordRouter billed cost,Estimated cost',
  'the strip keeps three figures and drops both agent comparisons');
assert(!/(?:7|30)d/.test(heroText), 'overview headings contain no period suffix');
assert(heroText.includes('Total tokens') && heroText.includes('30'), 'default overview uses canonical 30d total');
let heroPills = all(registry.get('hero')).filter((e) => e.classList.contains('pill'));
assertEqual(heroPills.map((e) => e.textContent).join(','),
  'direct NordRouter 20,other OpenClaw 10,other Hermes 6',
  '30d overview keeps a separate chip per agent');
assert(heroPills.every((e) => e.className === 'pill'), 'source chips keep the pill visual hook');
assert(heroText.includes('NordRouter billed cost') && heroText.includes('$0.4000'), 'direct billed cost shown');
assert(heroText.includes('Estimated cost') && heroText.includes('$0.3000'),
  'agent estimates are summed on one line');
assert(!heroText.includes('comparison'), 'agent NordRouter comparisons are gone from the strip');
assert(registry.get('models').textContent.includes('z-ai/glm-5.3'), 'direct model appears in unified table');
assertEqual(registry.get('models').children.map((row) => routeName(row)).join(','),
  'nordrouter,NordRouter,xai,openai', 'one row per model, labelled by its route');
assert(!registry.get('models').textContent.includes('OpenClaw') &&
  !registry.get('models').textContent.includes('Hermes'),
  'the agent split stays out of the table for now');
assert(registry.get('models-note').textContent.includes('billed USD'), 'table note explains cost provenance');
assert(registry.get('week').textContent.includes('z-ai/glm-5.3'), 'selected top models use direct model window');
const chart = registry.get('history');
let segments = all(chart).filter((e) => e.tagName === 'RECT');
assertEqual(segments.length, 3, '30d draws one segment per model');
assertEqual(new Set(segments.map((e) => e.getAttribute('fill'))).size, 3, 'models have distinct colours');
assertEqual(all(chart).filter((e) => e.classList.contains('legend-item')).length, 0,
  'history no longer repeats the model list');
assertEqual(modelSwatch('grok-test', 'xai').style.background, chartColor('grok-test'),
  'OpenClaw model row identifies its chart segment');
assertEqual(modelSwatch('hermes-model', 'openai').style.background, chartColor('hermes-model'),
  'Hermes-reported model row identifies its chart segment');
assertEqual(modelSwatch('nr-route', 'nordrouter').style.background, chartColor('nr-route'),
  'attributed NordRouter route identifies its chart segment');
assert(!modelSwatch('z-ai/glm-5.3', 'NordRouter'),
  'direct-only model has no invented daily chart attribution');
assert(!chart.textContent.includes('all models; daily split unavailable'), 'NordRouter route uses model-day data');
assert(segments.some((e) => segmentLabel(e).includes('nr-route · 20 tokens')) &&
  !segments.some((e) => segmentLabel(e).includes('nr-route · 21 tokens')) &&
  !segments.some((e) => segmentLabel(e).includes('nr-route · 26 tokens')),
  'direct NordRouter daily total replaces both agents comparison-only route totals');
assert(segments.every((e) => segmentLabel(e).includes('day total 36')), 'history excludes the 26-token duplicate');
assertEqual(all(chart).filter((e) => e.tagName === 'TEXT' && e.textContent === '03.10.2026').at(-1).getAttribute('text-anchor'),
  'end', 'last date label stays inside the chart');
let summary = all(chart).filter((e) => e.classList.contains('chart-summary'));
assertEqual(summary.length, 1, 'history has one summary line');
assertEqual(summary[0].textContent, 'daily tokens · peak 36', 'linear summary excludes total and estimate note');
const initialGrokColor = chartColor('grok-test');
const initialRouteColor = chartColor('nr-route');
const changedOrder = { ...MODELS, models: [...MODELS.models].reverse().concat({
  source: 'openclaw', provider: 'local', model: 'a-first', totals: { total: 1 },
  daily: [{ date: '2026-10-03', total: 1 }],
}) };
renderHistory(changedOrder);
renderModels(changedOrder);
assertEqual(chartColor('grok-test'), initialGrokColor, 'another model and input order do not recolour Grok');
assertEqual(chartColor('nr-route'), initialRouteColor, 'another model and input order do not recolour route');
assertEqual(modelSwatch('grok-test', 'xai').style.background, initialGrokColor,
  'table swatch follows stable chart colour after refresh');
registry.get('model-sort-model').listeners.click[0]();
assertEqual(modelSwatch('nr-route', 'nordrouter').style.background, initialRouteColor,
  'sorting table does not recolour charted model');
registry.get('model-sort-model').listeners.click[0]();
registry.get('model-sort-model').listeners.click[0]();
renderHistory({ ...MODELS, source_daily: { nordrouter: [] } });
renderModels(MODELS);
assert(!modelSwatch('nr-route', 'nordrouter'),
  'a route with no direct daily tokens must not claim chart attribution');
assertEqual(modelSwatch('grok-test', 'xai').style.background, initialGrokColor,
  'other OpenClaw history remains identified when direct daily data is absent');
renderPeriod(MODELS, QUOTAS);
windowListeners.resize();
assertEqual(all(chart).filter((e) => e.tagName === 'RECT').length, 3, 'resize keeps model segments');
assert(all(chart).filter((e) => e.tagName === 'RECT').every((e) => segmentLabel(e).includes('day total 36')),
  'resize keeps deduplicated history');
selectedDays = 7;
renderPeriod({ ...MODELS, meta: { ...MODELS.meta, days: 7 }, totals: { total: 11 },
  sources: { nordrouter: { tokens: 4, billed_cost_usd: 0.1, complete: true },
    openclaw: { tokens: 5, estimated_cost_usd: 0.05, complete: true },
    openclaw_nordrouter_comparison_tokens: 7,
    hermes: { tokens: 2, estimated_cost_usd: 0.02, unpriced_models: 0, complete: true },
    hermes_nordrouter_comparison_tokens: 3 },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'direct-7d', cost_usd: 0.1, totals: { total: 4 }, daily: [] },
    { source: 'openclaw', provider: 'xai', model: 'grok-7d', cost_usd: 0.05, totals: { total: 5 }, daily: [{ date: '2026-10-03', total: 5 }] },
    { source: 'openclaw', provider: 'nordrouter', model: 'nr-7d', cost_usd: 0.2, totals: { total: 7 }, daily: [{ date: '2026-10-03', total: 7 }] },
    { source: 'hermes', provider: 'openai', model: 'hermes-7d', cost_usd: 0.02, cost_kind: 'estimated', totals: { total: 2 }, daily: [{ date: '2026-10-03', total: 2 }] },
  ], source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 4 }] } }, QUOTAS);
assert(registry.get('hero').textContent.includes('Total tokens') && registry.get('hero').textContent.includes('11'), '7d overview');
heroPills = all(registry.get('hero')).filter((e) => e.classList.contains('pill'));
assertEqual(heroPills.map((e) => e.textContent).join(','),
  'direct NordRouter 4,other OpenClaw 5,other Hermes 2',
  '7d overview updates both agent chips');
assert(registry.get('models').textContent.includes('direct-7d'), '7d direct model row');
assert(registry.get('models').textContent.includes('grok-7d'), '7d OpenClaw table');
assert(registry.get('models').textContent.includes('hermes-7d'), '7d Hermes table');
assert(all(chart).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('day total 11')), '7d history');
segments = all(chart).filter((e) => e.tagName === 'RECT');
assertEqual(segments.length, 3, '7d draws both agent segments and the direct route');
assertEqual(new Set(segments.map((e) => e.getAttribute('fill'))).size, 3, '7d models have distinct colours');
assert(segments.some((e) => segmentLabel(e).includes('nr-7d · 4 tokens')), '7d NordRouter segment reconciles to direct source');
summary = all(chart).filter((e) => e.classList.contains('chart-summary'));
assertEqual(summary.length, 1, '7d has one summary line');
assertEqual(summary[0].textContent, 'daily tokens · peak 11', '7d linear summary excludes total and estimate note');
assert(registry.get('metrics').textContent.includes('Billed spend'), 'NordRouter metric follows selection');
assert(!registry.get('selected-top-label').textContent.includes('7d'), 'top-model label has no period suffix');
Object.entries(sectionNotes).forEach(([id, value]) => {
  assertEqual(registry.get(id).textContent, value, '7d ' + id + ' note stays static');
});
renderHistory({ models: [
  { source: 'openclaw', provider: 'nordrouter', model: 'A', daily: [{ date: '2026-10-03', total: 3 }] },
  { source: 'openclaw', provider: 'nordrouter', model: 'B', daily: [{ date: '2026-10-03', total: 2 }] },
  { source: 'openclaw', provider: 'xai', model: 'C', daily: [{ date: '2026-10-03', total: 5 }] },
], source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 7 }] } });
assert(all(chart).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('A · 4 tokens')) &&
  all(chart).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('B · 3 tokens')),
  'NordRouter model shares use largest-remainder allocation to exact direct total');
assert(all(chart).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('day total 12')),
  'allocated model bars preserve canonical day total');
assertEqual(new Set(all(chart).filter((e) => e.tagName === 'RECT').map((e) => e.getAttribute('fill'))).size, 3,
  'three individual models keep distinct colours');
const sharedHistory = { models: [
  { source: 'openclaw', provider: 'nordrouter', model: 'Shared', daily: [{ date: '2026-10-03', total: 3 }] },
  { source: 'openclaw', provider: 'xai', model: 'Shared', daily: [{ date: '2026-10-03', total: 5 }] },
  { source: 'openclaw', provider: 'local', model: 'Shared', daily: [{ date: '2026-10-03', total: 2 }] },
  { source: 'openclaw', provider: 'openai', model: 'Other', daily: [{ date: '2026-10-03', total: 1 }] },
], source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 4 }] } };
renderHistory(sharedHistory);
renderModels(sharedHistory);
assertEqual(all(chart).filter((e) => e.classList.contains('legend-item')).length, 0,
  'shared model does not recreate the duplicate legend');
segments = all(chart).filter((e) => e.tagName === 'RECT');
assertEqual(segments.length, 2, 'same model draws one combined segment per day');
assert(segments.some((e) => segmentLabel(e).includes('Shared · 11 tokens · day total 12')),
  'combined model includes direct-allocated NordRouter and both other providers exactly once');
assertEqual(new Set(segments.map((e) => e.getAttribute('fill'))).size, 2,
  'one colour is assigned per distinct model');
assert(['nordrouter'].every((source) =>
  modelSwatch('Shared', source).style.background === chartColor('Shared')),
  'the merged row of a shared model maps to its one chart colour');
assertEqual(registry.get('models').children.filter((row) => row.children[1].textContent.startsWith('Shared')).length, 1,
  'a model reported on several routes still occupies one row');
assertEqual(historyScale, 'linear', 'linear is the default history scale');
assert(all(chart).filter((e) => e.tagName === 'SVG')[0].getAttribute('aria-label').includes('linear token axis'),
  'initial history chart has a token axis');
assertEqual(all(chart).filter((e) => e.tagName === 'TITLE').length, 0,
  'linear chart has no native SVG tooltip');
assertEqual(all(chart).filter((e) => e.tagName === 'TEXT').slice(0, 3).map((e) => e.textContent).join(','),
  '12,6,0', 'initial axis is labelled in tokens');
const linearOther = segments.find((e) => segmentLabel(e).includes('Other · 1 tokens'));
const linearOtherHeight = Number(linearOther.getAttribute('height'));
const linearColors = new Map(segments.map((e) => [segmentLabel(e).split(' · ')[0], e.getAttribute('fill')]));
registry.get('history-scale').listeners.click[0]();
assertEqual(historyScale, 'balanced', 'scale button exposes balanced view');
assertEqual(registry.get('history-scale').textContent, 'Scale: balanced', 'button names weighted state');
assertEqual(registry.get('history-scale').getAttribute('aria-pressed'), 'true', 'balanced button sets pressed state');
assert(all(chart).filter((e) => e.tagName === 'SVG')[0].getAttribute('aria-label').includes('not a token axis'),
  'balanced axis cannot be mistaken for token counts');
assertEqual(all(chart).filter((e) => e.tagName === 'TEXT').slice(0, 3).map((e) => e.textContent).join(','),
  '100%,50%,0%', 'weighted axis is labelled as relative visual weight');
segments = all(chart).filter((e) => e.tagName === 'RECT');
const balancedOther = segments.find((e) => segmentLabel(e).includes('Other · 1 tokens'));
assert(Number(balancedOther.getAttribute('height')) > linearOtherHeight,
  'balanced scale makes the small model segment more legible');
assertEqual(segments.length, 2, 'balanced mode keeps one segment per model/day');
assertEqual(new Map(segments.map((e) => [segmentLabel(e).split(' · ')[0], e.getAttribute('fill')])).get('Shared'),
  linearColors.get('Shared'), 'scale toggle preserves model colour');
assert(segments.some((e) => segmentLabel(e).includes('Shared · 11 tokens · day total 12')),
  'both views preserve actual model and day totals');
registry.get('history-scale').listeners.click[0]();
assertEqual(historyScale, 'linear', 'scale button restores linear view');
assertEqual(registry.get('history-scale').textContent, 'Scale: linear', 'button visibly names token scale');
assertEqual(registry.get('history-scale').getAttribute('aria-pressed'), 'false', 'linear button clears pressed state');
renderHistory({ models: [
  { source: 'openclaw', provider: 'local', model: 'Tiny', daily: [
    { date: '2026-10-02', total: 0 }, { date: '2026-10-03', total: 1 }] },
], source_daily: { nordrouter: [] } });
segments = all(chart).filter((e) => e.tagName === 'RECT');
assertEqual(segments.length, 1, 'zero day draws no false segment');
assert(Number.isFinite(Number(segments[0].getAttribute('height'))) && Number(segments[0].getAttribute('height')) > 0,
  'linear scale handles a one-token day');
assertEqual(all(chart).filter((e) => e.tagName === 'TEXT').slice(0, 3).map((e) => e.textContent).join(','),
  '1,1,0', 'one-token linear axis has token tick labels');
registry.get('history-scale').listeners.click[0]();
assertEqual(historyScale, 'balanced', 'scale button restores balanced view');
assertEqual(registry.get('history-scale').textContent, 'Scale: balanced', 'button visibly names weighted state');
assertEqual(registry.get('history-scale').getAttribute('aria-pressed'), 'true', 'balanced button exposes pressed state');
assertEqual(all(chart).filter((e) => e.tagName === 'TITLE').length, 0,
  'balanced chart has no native tooltip; accessible scale label remains on SVG');
assert(all(chart).some((e) => e.tagName === 'SVG' && e.getAttribute('aria-label').includes('fourth-root')),
  'balanced SVG retains an accessible scale description');
renderHistory({ models: [
  { source: 'openclaw', provider: 'local', model: 'Huge', daily: [{ date: '2026-10-03', total: 1000000 }] },
  { source: 'openclaw', provider: 'local', model: 'Small', daily: [{ date: '2026-10-03', total: 1000 }] },
], source_daily: { nordrouter: [] } });
segments = all(chart).filter((e) => e.tagName === 'RECT');
const big = segments.find((e) => segmentLabel(e).includes('Huge · 1.0M tokens'));
const small = segments.find((e) => segmentLabel(e).includes('Small · 1.0K tokens'));
assert(Number(small.getAttribute('height')) / Number(big.getAttribute('height')) > 0.15,
  'fourth-root composition keeps a thousand-token model visible beside a million-token model');
assert(segments.every((e) => segmentLabel(e).includes('day total 1.0M')),
  'visual weighting never changes raw totals');
renderHistory({ models: [
  { source: 'openclaw', provider: 'local', model: 'gpt-6.1-sol', daily: [{ date: '2026-10-03', total: 51659130 }] },
  { source: 'openclaw', provider: 'local', model: 'Other', daily: [{ date: '2026-10-03', total: 1020708 }] },
], source_daily: { nordrouter: [] } });
segments = all(chart).filter((e) => e.tagName === 'RECT');
const sol = segments.find((e) => segmentLabel(e).startsWith('gpt-6.1-sol'));
const other = segments.find((e) => segmentLabel(e).startsWith('Other'));
const tip = all(chart).find((e) => e.classList.contains('history-tooltip'));
assertEqual(sol.children.length, 0, 'segment has no native SVG title to duplicate custom tooltip');
assertEqual(segmentLabel(sol), 'gpt-6.1-sol · 51.7M tokens · day total 52.7M',
  'tooltip shares Model Usage compact token formatter and omits date');
assert(!segmentLabel(sol).includes('2026') && !segmentLabel(sol).includes('51,659,130'),
  'accessible label omits date and raw token counts');
assert(all(chart).some((e) => e.tagName === 'TEXT' && e.textContent === '03.10.2026'),
  'date remains below the bar');
const touch = (rect, type, id, x, y) => rect.listeners[type][0]({ pointerType: 'touch', pointerId: id,
  clientX: x, clientY: y, target: rect });
touch(sol, 'pointerdown', 1, 380, 20);
touch(sol, 'pointerup', 1, 380, 20);
sol.listeners.focus[0](); // Chromium focuses a tabindex SVG rect after a touch tap.
assertEqual(tip.style.visibility, 'visible', 'tap opens persistent tooltip');
assertEqual(tooltipText(chart), segmentLabel(sol), 'visible tooltip text matches compact accessible label');
assert(Number.parseInt(tip.style.left, 10) >= 8 && Number.parseInt(tip.style.left, 10) <= 202 &&
  Number.parseInt(tip.style.top, 10) >= 8, 'tooltip is clamped to mobile viewport');
touch(other, 'pointerdown', 2, 200, 30);
touch(other, 'pointerup', 2, 200, 30);
sol.listeners.blur[0]();
other.listeners.focus[0]();
assertEqual(tooltipText(chart), segmentLabel(other), 'another segment replaces the touched tooltip');
assertEqual(tip.style.visibility, 'visible', 'blur of prior touched segment does not hide the new one');
touch(other, 'pointerdown', 3, 200, 30);
touch(other, 'pointerup', 3, 200, 30);
assertEqual(tip.style.visibility, 'hidden', 'second tap dismisses tooltip');
touch(sol, 'pointerdown', 4, 100, 100);
touch(sol, 'pointerup', 4, 100, 130);
assertEqual(tip.style.visibility, 'hidden', 'scroll-like movement does not open tooltip');
sol.listeners.pointerenter[0]({ pointerType: 'mouse', clientX: 120, clientY: 150 });
assertEqual(tip.style.visibility, 'visible', 'desktop hover opens tooltip');
sol.listeners.pointerleave[0]({ pointerType: 'mouse' });
assertEqual(tip.style.visibility, 'hidden', 'desktop pointer leave closes tooltip');
touch(sol, 'pointerdown', 5, 100, 100);
touch(sol, 'pointerup', 5, 100, 100);
windowListeners.pointerdown({ target: registry.get('hero') });
assertEqual(tip.style.visibility, 'hidden', 'outside tap closes tooltip');
touch(sol, 'pointerdown', 6, 100, 100);
touch(sol, 'pointerup', 6, 100, 100);
windowListeners.scroll();
assertEqual(tip.style.visibility, 'hidden', 'scroll dismisses an already open touch tooltip');
renderHero({ sources: { nordrouter: { complete: false }, openclaw: { complete: true, tokens: 5 } }, totals: { total: null } });
assert(registry.get('hero').textContent.includes('incomplete source · total unavailable'), 'missing source is not zero');
assertEqual(all(registry.get('hero')).filter((e) => e.classList.contains('pill')).length, 0,
  'incomplete overview must not show numeric source chips');
selectedDays = 30;

/* ---------------- unified model rows and sorting ---------------- */
const mergedFixture = { ...MODELS, models: [
  { source: 'nordrouter', provider: 'nordrouter', model: 'z-ai/glm-5.3',
    totals: { total: 1234567 }, cost_usd: 2.185573, daily: [] },
  { source: 'openclaw', provider: 'nordrouter', model: 'z-ai/glm-5.3',
    totals: { total: 765432, input: 70 }, cost_usd: 9, daily: [] },
  { source: 'hermes', provider: 'nordrouter', model: 'z-ai/glm-5.3',
    totals: { total: 111222, input: 30 }, cost_usd: null, daily: [] },
  { source: 'nordrouter', provider: 'nordrouter', model: 'google/gemini-3.6-flash',
    totals: { total: 42 }, cost_usd: 0.000012, daily: [] },
  { source: 'openclaw', provider: 'nordrouter', model: 'unmatched-route',
    totals: { total: 11 }, cost_usd: 3, daily: [] },
  { source: 'openclaw', provider: 'xai', model: 'other',
    totals: { total: 8 }, cost_usd: 0.555496, daily: [] },
] };
const originalRows = JSON.stringify(mergedFixture.models);
renderModels(mergedFixture);
assertEqual(registry.get('models').children.length, 4, 'matched route becomes one billed row');
const rowFor = (name) => registry.get('models').children.find((row) => row.children[1].textContent === name);
const matchedRow = rowFor('z-ai/glm-5.3');
assertEqual(routeName(matchedRow), 'NordRouter', 'direct model source has its original label');
assertEqual(routeName(rowFor('unmatched-route')), 'nordrouter', 'unmatched route source is unchanged');
assertEqual(routeName(rowFor('other')), 'xai', 'other provider source is unchanged');
const struck = all(matchedRow.children[6]).filter((e) => e.tagName === 'S');
assertEqual(struck.length, 1, 'one crossed-out figure per model, not one per agent');
assertEqual(struck[0].textContent, '876.7K', 'the crossed-out figure sums every agent report');
assert(matchedRow.children[6].textContent.endsWith('1.2M'), 'direct tokens use compact formatting');
assert(struck[0].title.includes('876,654') &&
  all(matchedRow.children[6]).find((e) => e.classList.contains('direct')).title.includes('1,234,567'),
  'exact underlying totals remain accessible in titles');
assert(struck[0].title.startsWith('Agent-reported'), 'the struck figure names its provenance');
assertEqual(matchedRow.children[7].textContent, '$2.19billed',
  'billed USD rounds to two decimals and replaces route estimate');
assertEqual(matchedRow.children[2].textContent, '100',
  'comparison token categories sum both agents and retain provenance');
const directOnly = rowFor('google/gemini-3.6-flash');
assertEqual(all(directOnly.children[6]).filter((e) => e.tagName === 'S').length, 0,
  'direct-only model has no invented agent comparison');
assertEqual(directOnly.children[7].textContent, '$0.00billed',
  'small billed cost still displays exactly two decimals');
assertEqual(rowFor('other').children[7].textContent, '$0.56estimated',
  'estimated USD also rounds to exactly two decimals');
assert(rowFor('unmatched-route').children[7].textContent === '$3.00estimated',
  'agent-only route keeps its estimate but is labelled estimated');
assertEqual(JSON.stringify(mergedFixture.models), originalRows, 'table merge never mutates API models');
/* Spellings that bill as one model share a model_key and must collapse. */
renderModels({ models: [
  { source: 'nordrouter', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash:fjord',
    model_key: 'deepseek-v4.1-flash', totals: { total: 100 }, cost_usd: 1, daily: [] },
  { source: 'nordrouter', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash',
    model_key: 'deepseek-v4.1-flash', totals: { total: 50 }, cost_usd: 0.5, daily: [] },
  { source: 'openclaw', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash',
    model_key: 'deepseek-v4.1-flash', totals: { total: 30 }, cost_usd: 9, daily: [] },
  { source: 'hermes', provider: 'openai', model: 'gpt-6-sol-900k',
    model_key: 'gpt-6-sol', totals: { total: 7 }, cost_usd: null, daily: [] },
  { source: 'openclaw', provider: 'openai', model: 'gpt-6-sol',
    model_key: 'gpt-6-sol', totals: { total: 3 }, cost_usd: 0.1, daily: [] },
  { source: 'hermes', provider: 'nordrouter', model: 'minimax-m3',
    model_key: 'minimax-m3', totals: { total: 1 }, cost_usd: null, daily: [] },
  { source: 'nordrouter', provider: 'nordrouter', model: 'minimax/minimax-m3',
    model_key: 'minimax-m3', totals: { total: 60 }, cost_usd: 0.6, daily: [] },
  { source: 'openclaw', provider: 'nordrouter', model: 'minimax/minimax-m3',
    model_key: 'minimax-m3', totals: { total: 40 }, cost_usd: 0, daily: [] },
] });
assertEqual(registry.get('models').children.length, 3, 'routing and context variants collapse into one row each');
const flashRow = registry.get('models').children.find((row) => row.children[1].textContent.startsWith('deepseek/deepseek-v4.1-flash'));
assertEqual(flashRow.children[1].textContent, 'deepseek/deepseek-v4.1-flash',
  'the most common spelling labels the merged row');
assertEqual(flashRow.children[6].textContent, '30150', 'both billed spellings are summed before the crossed-out agent total');
assertEqual(flashRow.children[7].textContent, '$1.50billed', 'billed cost of both spellings is summed');
const solRow = registry.get('models').children.find((row) => row.children[1].textContent === 'gpt-6-sol');
assertEqual(solRow.children[6].textContent, '10', 'agent rows of one model are summed');
assertEqual(solRow.children[7].textContent, '$0.10estimated', 'agent cost of one model is summed');
const m3Row = registry.get('models').children.find((row) => row.children[1].textContent === 'minimax/minimax-m3');
assertEqual(m3Row.children[1].textContent, 'minimax/minimax-m3',
  'the most common spelling labels the row even when a bare alias is shorter');
assertEqual(m3Row.children[6].textContent, '4160',
  'every spelling of one model lands in a single row: crossed-out agent total, then direct');

/* Hermes cost is derived when the page loads each source separately. */
const partialState = {
  days: 7, quotaParts: {}, quotas: null, quotaPending: {}, errors: {}, requests: {}, parts: {
    openclaw: { meta: { days: 7, openclaw_status: 'ok', start_date: 'p', end_date: 'q' },
      sources: { openclaw: { tokens: 9000, estimated_cost_usd: 9, priced_models: 1, unpriced_models: 0, complete: true } },
      models: [{ source: 'openclaw', provider: 'openai', model: 'gpt-6-sol', model_key: 'gpt-6-sol',
        totals: { total: 9000 }, cost_usd: 9, daily: [] }] },
    hermes: { meta: { days: 7, hermes_status: 'ok', start_date: 'p', end_date: 'q' },
      sources: { hermes: { tokens: 1000, estimated_cost_usd: null, priced_models: 0, unpriced_models: 1, complete: true } },
      models: [{ source: 'hermes', provider: 'openai', model: 'gpt-6-sol-900k', model_key: 'gpt-6-sol',
        totals: { total: 1000 }, cost_usd: null, daily: [] }] },
    nordrouter: { meta: { days: 7, nordrouter_status: 'ok', start_date: 'p', end_date: 'q' },
      sources: { nordrouter: { tokens: 0, billed_cost_usd: 0, complete: true } }, models: [] },
  },
};
renderAvailable(partialState);
const partialHero = registry.get('hero').textContent;
assert(partialHero.includes('Estimated cost') && partialHero.includes('$10.00'),
  'a Hermes row borrows the rate its model already reports from OpenClaw');
assert(!partialHero.includes('unpriced'),
  'a derived rate clears the unpriced marker instead of leaving cost blank');

const SORT_MODELS = { ...MODELS, models: [
  { source: 'openclaw', provider: 'xai', model: 'Alpha', account: 'unknown', cost_usd: 1,
    totals: { total: 10, input: 8, output: 1, cache_read: 1, cache_write: 0 }, daily: [] },
  { source: 'openclaw', provider: 'anthropic', model: 'Zulu', cost_usd: 3,
    totals: { total: 30, input: 4, output: 20, cache_read: 5, cache_write: 1 }, daily: [] },
  { source: 'openclaw', provider: 'xai', model: 'Beta', cost_usd: null,
    totals: { total: 20, input: 12, output: 3, cache_read: 4, cache_write: 1 }, daily: [] },
] };
const modelOrder = () => registry.get('models').children.map((row) => row.children[1].textContent).join(',');
const sortClick = (key) => registry.get('model-sort-' + key).listeners.click[0]();
assert(['source', 'model', 'input', 'output', 'cache_read', 'cache_write', 'total', 'usd']
  .every((key) => registry.get('model-sort-' + key).listeners.click.length === 1),
  'every model table header must be clickable');
renderModels(SORT_MODELS);
assertEqual(modelOrder(), 'Zulu,Beta,Alpha', 'base order is total tokens descending, not cost');
assert(!registry.get('models').textContent.includes('account:'), 'model table must ignore a legacy invented account');
assertEqual(registry.get('model-sort-head-total').getAttribute('aria-sort'), 'descending', 'default total header state');
sortClick('source');
assertEqual(modelOrder(), 'Alpha,Beta,Zulu', 'route descending uses displayed route name');
assertEqual(registry.get('model-sort-head-source').getAttribute('aria-sort'), 'descending', 'route sort announced');
sortClick('source');
assertEqual(modelOrder(), 'Zulu,Alpha,Beta', 'route ascending uses displayed route name');
sortClick('source');
assertEqual(modelOrder(), 'Zulu,Beta,Alpha', 'route third click resets to total descending');
sortClick('input');
assertEqual(modelOrder(), 'Beta,Alpha,Zulu', 'numeric input descending');
assertEqual(registry.get('model-sort-head-input').getAttribute('aria-sort'), 'descending', 'input sort announced');
sortClick('input');
assertEqual(modelOrder(), 'Zulu,Alpha,Beta', 'second click sorts ascending');
assertEqual(registry.get('model-sort-head-input').getAttribute('aria-sort'), 'ascending', 'ascending announced');
sortClick('input');
assertEqual(modelOrder(), 'Zulu,Beta,Alpha', 'third click resets to total descending');
assertEqual(registry.get('model-sort-head-total').getAttribute('aria-sort'), 'descending', 'reset state announced');
sortClick('model');
assertEqual(modelOrder(), 'Zulu,Beta,Alpha', 'text model desc uses lexical order');
sortClick('usd');
assertEqual(modelOrder(), 'Zulu,Alpha,Beta', 'different column starts descending and missing cost stays last');
sortClick('usd');
assertEqual(modelOrder(), 'Alpha,Zulu,Beta', 'missing cost stays last when ascending');
sortClick('usd');
assertEqual(modelOrder(), 'Zulu,Beta,Alpha', 'cost third click resets to total descending');
assert(registry.get('models').textContent.includes('Zulu') && registry.get('models').textContent.includes('Beta'),
  'sorting preserves row details');

/* ---------------- snapshot time ---------------- */
// The header must use the offset-aware field and render it in local time with
// a relative age, never the naive server string relabelled as local time.
// Timestamps are relative to now so the staleness threshold stays meaningful.
const freshIso = new Date(Date.now() - 60000).toISOString();
const fresh = snapshotLabel(freshIso, '2026-10-03T12:51:45');
assert(fresh.text.includes('snapshot '), 'a snapshot label must be prefixed with snapshot');
assert(!fresh.text.includes('12:51:45'), 'a snapshot must not fall back to the naive server string');
assertEqual(fresh.stale, false, 'a one-minute-old snapshot must not be marked stale');

const old = snapshotLabel(new Date(Date.now() - 3 * 3600000).toISOString(), null);
assertEqual(old.stale, true, 'a three-hour-old snapshot must be marked stale');
assert(old.text.includes('3h ago'), 'a stale snapshot must show its relative age, got: ' + old.text);

// A legacy cached payload has no offset-aware field; it must not be presented
// as the viewer's local time.
const legacy = snapshotLabel(null, '2026-10-03T12:51:45');
assert(legacy.text.includes('server time'),
  'a legacy naive timestamp must be labelled as server time, not local time');
assertEqual(legacy.stale, true, 'a legacy naive timestamp must not be presented as current');
assert(legacy.text.includes('03.10.2026 12:51'), 'legacy snapshot uses day.month.year and 24-hour clock');
process.env.TZ = 'Asia/Tbilisi';
const fixedSnapshot = snapshotLabel('2026-10-09T09:23:00Z', null);
assert(fixedSnapshot.text.includes('09.10.2026 13:23'), 'header snapshot follows local 24-hour convention');
if (originalZone === undefined) delete process.env.TZ;
else process.env.TZ = originalZone;
assertEqual(relativeAge(Date.now() - 7 * 60000), '7m ago', 'relativeAge reports minutes');
assertEqual(relativeAge(Date.now()), 'just now', 'relativeAge reports a fresh timestamp');

/* ---------------- no unsafe DOM APIs ---------------- */
assert(!code.includes('innerHTML'), 'dashboard script must not use innerHTML');
assert(!code.includes('insertAdjacentHTML'), 'dashboard script must not use insertAdjacentHTML');
assert(!code.includes('document.write'), 'dashboard script must not use document.write');

console.log('OK: dashboard DOM contract holds');
selectedDays = initialSelectedDays;
setPeriodButtons();
registry.get('history-scale').listeners.click[0]();
assertEqual(historyScale, 'linear', 'progressive scenarios start with the default scale');

/* An accelerated timer tick must request a provider refresh and repaint from
   a changed backend snapshot without invoking the Refresh button. */
async function waitForReload() {
  for (let n = 0; n < 20; n++) {
    await new Promise((resolve) => setImmediate(resolve));
    if (!busy) return;
  }
  fail('reload did not finish');
}

(async () => {
  const flush = () => new Promise((resolve) => setImmediate(resolve));
  const request = (url, from = 0) => pendingFetches.slice(from).find((entry) => entry.url === url);
  const reply = (entry, body) => {
    assert(entry, 'missing pending fetch');
    entry.resolve({ ok: true, json: () => Promise.resolve(body) });
  };
  const urlFor = (days, source) => '/api/v1/model-breakdown?days=' + days + '&daily=true&source=' + source;
  const quotaFor = (source) => '/api/v1/quotas' + (source === 'all' ? '' : '?source=' + source);
  const quotaResponse = (url, used = 10) => {
    const source = url.includes('?source=') ? url.split('?source=')[1] : 'all';
    return { ...QUOTAS, quotas: QUOTAS.quotas.filter((row) => source === 'all' || row.provider === source)
      .map((row) => row.provider === 'codex' && row.account === 'codex_1'
        ? { ...row, used_percentage: used, remaining_percentage: 100 - used } : row) };
  };
  const sourceOf = (url) => url.includes('nordrouter') ? 'nordrouter'
    : (url.includes('source=hermes') ? 'hermes' : 'openclaw');
  const answer = (entry, days) => reply(entry, entry.url.startsWith('/api/v1/quotas')
    ? quotaResponse(entry.url) : modelFor(days, sourceOf(entry.url)));
  // Hermes contributes no tokens in this scenario: the point is that its part
  // must complete before the canonical total is published, not that it changes it.
  const modelFor = (days, source, date = '2026-10-03') => ({
    meta: { ...MODELS.meta, start_date: date, end_date: date, days,
      openclaw_status: source === 'openclaw' ? 'ok' : 'pending',
      hermes_status: source === 'hermes' ? 'ok' : 'pending',
      nordrouter_status: source === 'nordrouter' ? 'ok' : 'pending' },
    sources: source === 'nordrouter'
      ? { nordrouter: { tokens: days, billed_cost_usd: 0.125, complete: true },
          openclaw_nordrouter_comparison_tokens: 0 }
      : source === 'hermes'
        ? { hermes: { tokens: 0, estimated_cost_usd: null, unpriced_models: 0, complete: true },
            hermes_nordrouter_comparison_tokens: 0 }
        : { openclaw: { tokens: 2, estimated_cost_usd: 0.03, complete: true },
            openclaw_nordrouter_comparison_tokens: 0 },
    source_daily: { nordrouter: source === 'nordrouter' ? [{ date, tokens: days }] : [] },
    models: source === 'nordrouter'
      ? [{ source, provider: source, model: 'direct-' + days + 'd', cost_usd: 0.125,
          totals: { total: days }, daily: [] }]
      : source === 'hermes' ? []
      : [{ source, provider: 'xai', model: 'grok-' + days + 'd', cost_usd: 0.03,
          totals: { total: 2 }, daily: [{ date, total: 2 }] }],
  });
  const clickPeriod = (days) => registry.get('period-' + days).listeners.click[0]();
  const settle = async () => { await flush(); await flush(); };

  // Initial GETs begin alongside a deliberately stalled full refresh.
  const initialUpdate = request('/api/v1/display/update');
  assert(initialUpdate, 'initial provider update started');
  assertEqual(initialSelectedDays, 7, 'initial selection is seven days');
  assertEqual(initialPressed[0], 'true', 'seven-day button starts pressed');
  assertEqual(initialPressed[1], 'false', 'thirty-day button starts unpressed');
  assertEqual(JSON.parse(initialUpdate.options.body).view, '7d', 'initial update requests seven days');
  assert(request(quotaFor('all')) && request(quotaFor('codex')) && request(quotaFor('grok')) &&
    request(quotaFor('grok_bot')) && request(urlFor(7, 'nordrouter')) &&
    request(urlFor(7, 'openclaw')) && request(urlFor(7, 'hermes')),
    'independent GETs start before full refresh completes');
  reply(request(quotaFor('codex')), quotaResponse(quotaFor('codex')));
  await settle();
  assert(cardFor(codexRoot, 'first@example.test'), 'Codex renders while Grok, cache, OpenClaw and update wait');
  assert(!registry.get('quotas').textContent.includes('Grok Build'), 'Grok is still pending');
  request(quotaFor('grok')).reject(new Error('slow Grok timeout'));
  await settle();
  assert(cardFor(codexRoot, 'first@example.test'), 'Grok failure retains Codex');
  assert(registry.get('error').textContent.includes('grok unavailable: slow Grok timeout'),
    'Grok failure is identified locally');
  const staleCache = quotaResponse(quotaFor('all'));
  staleCache.quotas = staleCache.quotas.map((row) => row.provider === 'codex' && row.account === 'codex_1'
    ? { ...row, used_percentage: 98, remaining_percentage: 2 } : row);
  reply(request(quotaFor('all')), staleCache);
  reply(request(quotaFor('grok_bot')), quotaResponse(quotaFor('grok_bot')));
  await settle();
  assert(cardFor(codexRoot, 'first@example.test')[0].textContent.includes('10% used'),
    'late stale cache cannot overwrite fresh Codex quota');
  reply(request(urlFor(7, 'nordrouter')), modelFor(7, 'nordrouter'));
  reply(request(urlFor(7, 'hermes')), modelFor(7, 'hermes'));
  await settle();
  assert(registry.get('models').textContent.includes('direct-7d'), 'direct model renders before OpenClaw');
  assert(all(registry.get('history')).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('day total 7')),
    'direct history renders before OpenClaw');
  assert(registry.get('hero').textContent.includes('incomplete source'), 'incomplete canonical total is not invented');
  request(urlFor(7, 'openclaw')).reject(new Error('slow Gateway timeout'));
  initialUpdate.reject(new Error('provider refresh failed'));
  await settle();
  assert(registry.get('models').textContent.includes('direct-7d'), 'Gateway failure retains direct model');
  assert(registry.get('quotas').textContent, 'quota section remains rendered');
  assert(registry.get('error').textContent.includes('openclaw unavailable: slow Gateway timeout'),
    'Gateway failure is identified locally');
  assert(!registry.get('period-1').disabled, 'period buttons stay usable during a failed refresh');

  // A period switch starts all sources without another full refresh. OpenClaw
  // can arrive first, then NordRouter completes the canonical total.
  const beforeOne = pendingFetches.length;
  clickPeriod(1);
  assertEqual(selectedDays, 1, 'one-day selection is immediate');
  assertEqual(registry.get('period-1').getAttribute('aria-pressed'), 'true', 'one-day button is pressed');
  assert(!pendingFetches.slice(beforeOne).some((entry) => entry.url === '/api/v1/display/update'),
    'period switch avoids provider refresh');
  reply(request(urlFor(1, 'openclaw'), beforeOne), modelFor(1, 'openclaw'));
  reply(request(urlFor(1, 'hermes'), beforeOne), modelFor(1, 'hermes'));
  reply(request(quotaFor('grok'), beforeOne), quotaResponse(quotaFor('grok')));
  await settle();
  assert(registry.get('quotas').textContent.includes('Grok Build'), 'Grok renders while Codex is pending');
  request(quotaFor('codex'), beforeOne).reject(new Error('slow Codex timeout'));
  await settle();
  assert(registry.get('quotas').textContent.includes('Grok Build'), 'Codex failure retains Grok');
  assert(registry.get('error').textContent.includes('codex unavailable: slow Codex timeout'),
    'Codex failure is identified locally');
  assert(registry.get('models').textContent.includes('grok-1d'), 'OpenClaw row renders before NordRouter');
  assert(!registry.get('models').textContent.includes('direct-7d'), 'new period does not mix old direct rows');
  reply(request(quotaFor('all'), beforeOne), quotaResponse(quotaFor('all')));
  reply(request(quotaFor('grok_bot'), beforeOne), quotaResponse(quotaFor('grok_bot')));
  reply(request(urlFor(1, 'nordrouter'), beforeOne), modelFor(1, 'nordrouter'));
  await settle();
  assert(registry.get('models').textContent.includes('direct-1d'), 'direct one-day row joins ready OpenClaw');
  assert(all(registry.get('history')).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('day total 3')),
    'both sources combine in linear chart');
  assert(registry.get('hero').textContent.includes('Total tokens') &&
    registry.get('hero').textContent.includes('3'), 'canonical one-day total completes');
  assert(registry.get('metrics').textContent.includes('$0.1250'), 'one-day billed spend uses direct source');

  // Late responses from superseded periods cannot repaint or report errors.
  const beforeThirty = pendingFetches.length;
  clickPeriod(30);
  const thirty = pendingFetches.slice(beforeThirty);
  const beforeSeven = pendingFetches.length;
  clickPeriod(7);
  const seven = pendingFetches.slice(beforeSeven);
  seven.forEach((entry) => answer(entry, 7));
  await settle();
  thirty.find((entry) => entry.url.includes('openclaw')).reject(new Error('late failure'));
  thirty.filter((entry) => !entry.url.includes('openclaw')).forEach((entry) => answer(entry, 30));
  await settle();
  assertEqual(selectedDays, 7, 'latest period remains selected');
  assert(registry.get('models').textContent.includes('direct-7d') &&
    !registry.get('models').textContent.includes('direct-30d'), 'late source cannot repaint latest period');
  assert(!registry.get('error').textContent.includes('late failure'), 'late error is ignored');

  // Same-period refresh preserves already visible data on one-source failure.
  const beforeRefresh = pendingFetches.length;
  registry.get('refresh').listeners.click[0]();
  const fresh = pendingFetches.slice(beforeRefresh);
  assert(fresh.some((entry) => entry.url === '/api/v1/display/update'), 'manual refresh starts full update');
  fresh.find((entry) => entry.url.includes('openclaw')).reject(new Error('current Gateway failure'));
  fresh.filter((entry) => entry.url !== '/api/v1/display/update' && !entry.url.includes('openclaw'))
    .forEach((entry) => answer(entry, 7));
  fresh.find((entry) => entry.url === '/api/v1/display/update').reject(new Error('refresh timeout'));
  await settle();
  assert(registry.get('models').textContent.includes('grok-7d') &&
    registry.get('models').textContent.includes('direct-7d'), 'failed refresh retains displayed sources');
  assert(registry.get('error').textContent.includes('openclaw unavailable: current Gateway failure'),
    'current source failure is visible');
  assert(!registry.get('refresh').disabled, 'Refresh re-enables after update failure');

  const beforeSuccessfulRefresh = pendingFetches.length;
  registry.get('refresh').listeners.click[0]();
  const successful = pendingFetches.slice(beforeSuccessfulRefresh);
  reply(successful.find((entry) => entry.url === '/api/v1/display/update'), PAYLOAD);
  await settle();
  assertEqual(pendingFetches.slice(beforeSuccessfulRefresh).filter((entry) =>
    entry.url === urlFor(7, 'openclaw')).length, 1,
  'full refresh does not duplicate a pending Gateway request');
  successful.find((entry) => entry.url === urlFor(7, 'openclaw')).resolve({
    ok: true, json: () => Promise.resolve(modelFor(7, 'openclaw')),
  });
  pendingFetches.slice(beforeSuccessfulRefresh).filter((entry) =>
    entry.url.startsWith('/api/v1/quotas') || entry.url === urlFor(7, 'nordrouter') ||
    entry.url === urlFor(7, 'hermes'))
    .forEach((entry) => answer(entry, 7));
  await settle();
  assert(!registry.get('error').textContent.includes('current Gateway failure'),
    'successful source request clears its earlier error');

  let updates = 0;
  let used = 11;
  let currentDay = '2026-10-03';
  global.fetch = (url, options) => {
    if (url === '/api/v1/display/update') {
      updates++;
      return Promise.resolve({ ok: true, json: () => Promise.resolve(PAYLOAD) });
    }
    const body = url.startsWith('/api/v1/quotas')
      ? { ...quotaResponse(url, used), generated_at_utc: new Date().toISOString() }
      : modelFor(selectedDays, sourceOf(url), currentDay);
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  };
  assert(typeof autoRefreshTick === 'function', 'automatic timer is installed');
  autoRefreshTick();
  await waitForReload();
  await settle();
  assert(cardFor(codexRoot, 'first@example.test')[0].textContent.includes('11% used'), 'first automatic quota rendered');
  used = 44;
  autoRefreshTick();
  await waitForReload();
  await settle();
  assert(cardFor(codexRoot, 'first@example.test')[0].textContent.includes('44% used'), 'changed quota repainted');
  assertEqual(updates, 2, 'each automatic tick starts one provider update');
  clickPeriod(1);
  await settle();
  currentDay = '2026-10-04';
  autoRefreshTick();
  await waitForReload();
  await settle();
  assert(registry.get('model-stamp').textContent.includes('04.10.2026 → 04.10.2026'),
    'midnight refresh selects new local day');
  assert(!registry.get('history').textContent.includes('03.10.2026'), 'old day is not charted');
  clickPeriod(30);
  await settle();
  assertEqual(selectedDays, 30, 'thirty-day period remains available');
  assert(all(registry.get('history')).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('day total 32')),
    'thirty-day history renders');
  assertEqual(historyScale, 'linear', 'progressive loads preserve linear scale default');
  console.log('OK: independent sources render progressively and preserve period/scale state');
})().catch((error) => { console.error(error); process.exitCode = 1; });
