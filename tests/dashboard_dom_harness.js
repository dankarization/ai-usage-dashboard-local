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
}

const registry = new Map();
['hero', 'quotas', 'history', 'codex', 'metrics', 'today', 'week', 'selected-top-label',
 'model-stamp', 'models', 'stamp', 'error', 'refresh', 'nr-status',
 'period-30', 'period-7', 'overview-note', 'history-note', 'models-note', 'costs-note', 'cost-models'].forEach((id) => {
  registry.set(id, new Element('div'));
});
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
global.window = { addEventListener: (type, fn) => { windowListeners[type] = fn; } };
let autoRefreshTick;
global.setInterval = (fn) => { autoRefreshTick = fn; return 0; };
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });

/* ---------------- run the real dashboard script ---------------- */
const code = fs.readFileSync(jsPath, 'utf8');
vm.runInThisContext(code, { filename: jsPath });

['renderQuotas', 'renderHero', 'renderHistory', 'renderModels', 'renderCosts', 'renderPeriod', 'severity', 'statusClass', 'providerName', 'tokens', 'usd', 'countdown', 'groupQuotas', 'effectiveStatus', 'brandMark', 'orderGroups', 'snapshotLabel', 'relativeAge', 'time', 'dateOnly', 'serverTime']
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
    openclaw_nordrouter_comparison_tokens: 21 },
  source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 20 }] },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'z-ai/glm-5.3', cost_usd: 0.4, totals: { total: 19 }, daily: [] },
    { source: 'openclaw', provider: 'nordrouter', model: 'nr-route', cost_usd: 0.5, totals: { total: 21 }, daily: [{ date: '2026-10-03', total: 21 }] },
    { source: 'openclaw', provider: 'xai', model: 'grok-test', cost_usd: 0.2, totals: { total: 10 }, daily: [{ date: '2026-10-03', total: 10 }] },
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
assert(grokCard.textContent.includes('Grok Build: 100.0%'), 'Grok must show the verified product name and percentage');
assert(grokCard.textContent.includes('not tokens or separate limits'), 'product percentage must not be represented as tokens or a separate limit');
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
let heroText = registry.get('hero').textContent;
assert(heroText.includes('Total tokens · 30d') && heroText.includes('30'), 'default overview uses canonical 30d total');
let heroPills = all(registry.get('hero')).filter((e) => e.classList.contains('pill'));
assertEqual(heroPills.map((e) => e.textContent).join(','), 'direct NordRouter 20,other OpenClaw 10',
  '30d overview shows separate source chips with their canonical values');
assert(heroPills.every((e) => e.className === 'pill'), 'source chips keep the pill visual hook');
assert(heroText.includes('NordRouter billed cost · 30d') && heroText.includes('$0.4000'), 'direct billed cost shown');
assert(heroText.includes('OpenClaw estimated cost · 30d') && heroText.includes('$0.2000'), 'estimate shown separately');
assert(heroText.includes('comparison') && heroText.includes('21'), 'comparison duplicate shown but excluded');
assert(registry.get('cost-models').textContent.includes('z-ai/glm-5.3'), 'direct model costs restored');
assert(!registry.get('cost-models').textContent.includes('nr-route'), 'comparison route is not billed table');
assertEqual(registry.get('models').children.map((row) => row.children[0].textContent).join(','), 'nordrouter,xai',
  'OpenClaw table displays only route names');
assert(!registry.get('models-note').textContent.includes('comparison only') &&
  registry.get('models-note').textContent.includes('excluded from Overview and history totals'),
  'table note explains exclusion without comparison-only wording');
assert(!registry.get('models').textContent.includes('z-ai/glm-5.3'), 'direct rows are not mixed into OpenClaw table');
assert(registry.get('week').textContent.includes('z-ai/glm-5.3'), 'selected top models use direct model window');
const chart = registry.get('history');
assertEqual(all(chart).filter((e) => e.tagName === 'RECT').length, 1, 'one real daily bucket drawn');
assert(chart.textContent.includes('total 30 tokens'), 'history excludes the 21-token duplicate');
windowListeners.resize();
assert(chart.textContent.includes('total 30 tokens'), 'resize keeps deduplicated history');
selectedDays = 7;
renderPeriod({ ...MODELS, meta: { ...MODELS.meta, days: 7 }, totals: { total: 9 },
  sources: { nordrouter: { tokens: 4, billed_cost_usd: 0.1, complete: true },
    openclaw: { tokens: 5, estimated_cost_usd: 0.05, complete: true },
    openclaw_nordrouter_comparison_tokens: 7 },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'direct-7d', cost_usd: 0.1, totals: { total: 4 }, daily: [] },
    { source: 'openclaw', provider: 'xai', model: 'grok-7d', cost_usd: 0.05, totals: { total: 5 }, daily: [{ date: '2026-10-03', total: 5 }] },
    { source: 'openclaw', provider: 'nordrouter', model: 'nr-7d', cost_usd: 0.2, totals: { total: 7 }, daily: [{ date: '2026-10-03', total: 7 }] },
  ], source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 4 }] } }, QUOTAS);
assert(registry.get('hero').textContent.includes('Total tokens · 7d') && registry.get('hero').textContent.includes('9'), '7d overview');
heroPills = all(registry.get('hero')).filter((e) => e.classList.contains('pill'));
assertEqual(heroPills.map((e) => e.textContent).join(','), 'direct NordRouter 4,other OpenClaw 5',
  '7d overview updates both source chips');
assert(registry.get('cost-models').textContent.includes('direct-7d'), '7d direct cost table');
assert(registry.get('models').textContent.includes('grok-7d'), '7d OpenClaw table');
assert(chart.textContent.includes('total 9 tokens'), '7d history');
assert(registry.get('metrics').textContent.includes('Billed spend · 7d'), 'NordRouter metric follows selection');
assert(registry.get('selected-top-label').textContent.includes('7d'), 'top-model period follows selection');
assert(registry.get('history-note').textContent.includes('7d') && registry.get('models-note').textContent.includes('7d'),
  'period labels follow selection');
renderHero({ sources: { nordrouter: { complete: false }, openclaw: { complete: true, tokens: 5 } }, totals: { total: null } });
assert(registry.get('hero').textContent.includes('incomplete source · total unavailable'), 'missing source is not zero');
assertEqual(all(registry.get('hero')).filter((e) => e.classList.contains('pill')).length, 0,
  'incomplete overview must not show numeric source chips');
selectedDays = 30;

/* ---------------- OpenClaw table sorting ---------------- */
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
  'every OpenClaw model table header must be clickable');
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
  await waitForReload();
  let updates = 0;
  let used = 11;
  global.fetch = (url, options) => {
    if (url === '/api/v1/display/update') {
      updates++;
      assertEqual(JSON.parse(options.body).reason, 'auto_refresh', 'timer must refresh providers');
      return Promise.resolve({ ok: true, json: () => Promise.resolve(PAYLOAD) });
    }
    if (url === '/api/v1/quotas') {
      const rows = { ...QUOTAS, generated_at_utc: new Date().toISOString(),
        quotas: QUOTAS.quotas.map((row) => row.provider === 'codex' && row.account === 'codex_1'
          ? { ...row, used_percentage: used, remaining_percentage: 100 - used } : row) };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(rows) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(url.includes('model-breakdown') ? MODELS : PAYLOAD) });
  };
  assert(typeof autoRefreshTick === 'function', 'automatic timer must be installed');
  autoRefreshTick();
  await waitForReload();
  assert(cardFor(codexRoot, 'first@example.test')[0].textContent.includes('11% used'), 'first automatic snapshot rendered');
  used = 44;
  autoRefreshTick();
  await waitForReload();
  assert(cardFor(codexRoot, 'first@example.test')[0].textContent.includes('44% used'), 'changed quota rendered without button');
  assertEqual(updates, 2, 'each automatic tick must issue one provider refresh');
  const calls = [];
  global.fetch = (url) => {
    calls.push(url);
    const body = url.includes('model-breakdown')
      ? { ...MODELS, meta: { ...MODELS.meta, days: 7 }, totals: { total: 9 },
          sources: { nordrouter: { tokens: 4, billed_cost_usd: 0.1, complete: true },
            openclaw: { tokens: 5, estimated_cost_usd: 0.05, complete: true },
            openclaw_nordrouter_comparison_tokens: 7 },
          source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 4 }] },
          models: [{ source: 'nordrouter', provider: 'nordrouter', model: 'direct-7d', cost_usd: 0.1,
            totals: { total: 4 }, daily: [] },
            { source: 'openclaw', provider: 'xai', model: 'grok-7d', cost_usd: 0.05,
              totals: { total: 5 }, daily: [{ date: '2026-10-03', total: 5 }] }] }
      : QUOTAS;
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  };
  registry.get('period-7').listeners.click[0]();
  await waitForReload();
  assertEqual(selectedDays, 7, 'toggle commits selected period after both sources load');
  assert(calls.some((url) => url.includes('days=7&daily=true')), 'toggle requests seven-day model data');
  assert(!calls.includes('/api/v1/display/update'), 'period switch does not force unrelated provider collection');
  assert(registry.get('hero').textContent.includes('Total tokens · 7d'), 'toggle repaints overview');
  assert(registry.get('history').textContent.includes('total 9 tokens'), 'toggle repaints history');
  assert(registry.get('cost-models').textContent.includes('direct-7d'), 'toggle repaints direct model costs');
  assert(registry.get('models').textContent.includes('grok-7d'), 'toggle repaints OpenClaw model usage');
  assertEqual(registry.get('period-7').getAttribute('aria-pressed'), 'true', 'toggle updates pressed state');
  console.log('OK: accelerated auto-refresh repainted changed provider data');
})().catch((error) => { console.error(error); process.exitCode = 1; });
