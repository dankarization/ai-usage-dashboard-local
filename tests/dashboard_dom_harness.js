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
['openai-api', 'hero', 'quotas', 'history', 'history-scale', 'codex', 'codex-brand', 'grok-brand', 'metrics', 'today', 'week', 'selected-top-label',
 'model-stamp', 'models', 'stamp', 'error', 'refresh', 'nr-status',
 'period-30', 'period-7', 'period-1', 'overview-note', 'models-note'].forEach((id) => {
  registry.set(id, new Element('div'));
});
const sectionNotes = {
  'overview-note': 'Canonical direct NordRouter + agent usage, summed per model',
  'models-note': 'History counts reported serving routes and direct agent usage once. Gray is verified unmatched NordRouter usage; incomplete coverage stays unresolved. Table history counts reconcile the chart; account-window balances and crossed-out agent comparisons are not additional tokens. Billing and router estimates are not additive.',
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
    { account: 'codex_1', account_label: 'first@example.test', plan_type: 'prolite', status: 'ok', provider: 'codex', label: '7d',
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
assertEqual(severity(100), 'full', 'only exact 100% uses the exhausted colour');
assertEqual(severity(81), 'danger', '81% retains the near-full warning colour');
assertEqual(severity(99.99), 'danger', 'rounding must not turn partial usage into exhausted');
assertEqual(severity(0), 'ok', 'zero is not exhausted');
assertEqual(statusClass('not_configured'), 'not_configured', 'statusClass(not_configured)');
assertEqual(statusClass('stale'), 'stale', 'statusClass(stale)');
assertEqual(statusClass('cached'), 'stale', 'a saved snapshot uses the stale colour');
assertEqual(statusText('cached'), 'Saved snapshot', 'a saved snapshot is labelled, never rendered as active data');
assertEqual(providerName('codex'), 'Codex', 'providerName(codex)');
assertEqual(providerName('grok'), 'Grok', 'providerName(grok)');
for (const [provider, label, paths] of [['codex', 'OpenAI (Codex)', 1], ['grok', 'Grok', 2], ['grok_bot', 'Grok', 2]]) {
  const mark = brandMark(provider);
  assertEqual(mark.textContent, '', 'official mark is not a text approximation');
  assertEqual(mark.getAttribute('role'), 'img', 'logo has an accessible image role');
  assertEqual(mark.getAttribute('aria-label'), label, 'logo identifies its provider without visible repetition');
  const svg = mark.children[0];
  assertEqual(svg.tagName, 'SVG', 'brand mark is actual inline SVG');
  assertEqual(svg.getAttribute('aria-hidden'), 'true', 'SVG does not duplicate the tile accessible name');
  assertEqual(svg.children.length, paths, 'official path count is preserved');
  assert(svg.children.every((p) => p.tagName === 'PATH' && p.getAttribute('d').length > 100), 'logo uses real nonempty paths');
  assert(all(svg).every((e) => !['SCRIPT', 'IMAGE', 'FOREIGNOBJECT', 'USE'].includes(e.tagName)), 'SVG contains only inert local geometry');
}
assertEqual(brandMark('nordrouter').textContent, 'NR', 'other sources keep their existing mark');
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

/* OpenAI API credit evidence must never masquerade as Codex or gross tokens. */
renderOpenaiApi([{provider:'openai_api', status:'unavailable', billing:{
  subsidy_total_usd:null, paid_usage_usd:null, detail:'Platform session required', fetched_at:new Date().toISOString()
}}]);
const apiRoot = registry.get('openai-api');
assert(!apiRoot.textContent.includes('Unknown'), 'missing grants do not create an Unknown wall');
assert(!apiRoot.textContent.includes('Paid / non-subsidized usage:'), 'paid spend is not inferred');
assert(!apiRoot.textContent.includes('Cycle:'), 'no guessed cycle or empty cycle fields');
assert(apiRoot.textContent.includes('Platform session required'), 'auth blocker is visible');
assert(!all(apiRoot).some((e) => e.getAttribute('role') === 'progressbar'), 'unknown subsidy has no zero bar');
renderOpenaiApi([{provider:'openai_api', status:'partial', billing:{aggregate_total_usd:100, aggregate_used_usd:0, aggregate_remaining_usd:100}}]);
assert(apiRoot.textContent.includes('$100.00 remaining · $0.00 used of $100.00 granted'), 'real aggregate zero is preserved');
assert(!apiRoot.textContent.includes('Subsidy total:'), 'aggregate is not subsidy');

renderOpenaiApi([{provider:'openai_api', status:'partial', label:'All credits (funding unclassified)', used_percentage:32.28, billing:{
  aggregate_total_usd:105, aggregate_remaining_usd:71.07, aggregate_used_usd:33.93, organization_name:'Example', purchased_balance_usd:6.11, unclassified_free_remaining_usd:65,
  benefit_period_amount_usd:100, benefit_cadence:'monthly', benefit_expires_at:'2026-11-21T19:23:47Z',
  benefit_checked_at:'2026-11-03T00:00:00Z', benefit_snapshot_expired:false,
  fetched_at:new Date().toISOString(),
  detail:'Credit-grant balances verified. Grant rows do not identify ChatGPT subsidy versus other funding. Purchased balance is reported separately when supplied. Cycle, paid spend and token funding allocation remain unknown.',
  grants:[{total_usd:65, remaining_usd:65, effective_at:'2026-11-01T00:00:00Z',expires_at:'2026-11-21T19:23:47Z'}]
}}]);
assert(apiRoot.textContent.includes('Purchased: $6.11'), 'purchased balance comes from paid field');
assert(!apiRoot.textContent.includes('Paid / non-subsidized usage:'), 'purchased balance is not paid usage');
assert(apiRoot.textContent.includes('Subscription entitlement: $100.00 / monthly (not the initial grant amount)'), 'entitlement is distinct from grant');
assert(apiRoot.textContent.includes('saved snapshot'), 'benefit verification is not advertised as live');
assert(apiRoot.textContent.includes('Grant (funding unclassified): $65.00 / $65.00'), 'unclassified grant is not relabelled subsidy');
assert(!apiRoot.textContent.includes('Subsidy total:'), 'entitlement does not fabricate current subsidy grant');
const apiBar = all(apiRoot).find((e) => e.getAttribute('role') === 'progressbar');
assertEqual(apiBar.getAttribute('aria-label'), 'All API credits used', 'aggregate bar names its actual pool');
assertEqual(apiBar.getAttribute('aria-valuenow'), '32', 'aggregate progress is authoritative');

const billingDisclosure = all(apiRoot).find((e) => e.tagName === 'DETAILS');
assert(billingDisclosure && !billingDisclosure.getAttribute('open'), 'technical billing details start closed');
const defaultApiText = apiRoot.children[0].children.filter((e) => e.tagName !== 'DETAILS').map((e) => e.textContent).join(' ');
assert(defaultApiText.includes('$71.07 remaining') && defaultApiText.includes('Purchased: $6.11'),
  'default card contains authoritative balances');
assert(!defaultApiText.includes('Unknown') && !defaultApiText.includes('Subscription entitlement') &&
  !defaultApiText.includes('Grant (funding'), 'default card omits technical and Unknown-field noise');
for (const removed of ['Credit-grant balances verified.', 'All-grant totals are not current-cycle spending.',
  'Checked ', 'Separate from Codex login.', 'Not added to local token history.', 'Balances are in dollars;',
  'this source does not split tokens by funding.']) {
  assert(!apiRoot.textContent.includes(removed), 'removed OpenAI explanation must not survive in disclosure: ' + removed);
}
renderOpenaiApi([{provider:'openai_api', status:'ok', used_percentage:20, billing:{
  aggregate_total_usd:100, aggregate_used_usd:20, aggregate_remaining_usd:80,
  fetched_at:new Date().toISOString(), detail:'A dynamically revised success explanation'
}}]);
assert(!apiRoot.textContent.includes('A dynamically revised success explanation'), 'dynamic success explanations stay removed');
assert(apiRoot.textContent.includes('$80.00 remaining · $20.00 used of $100.00 granted'), 'removing explanations preserves balances');
renderOpenaiApi([{provider:'openai_api', status:'unavailable', billing:{aggregate_total_usd:100,
  aggregate_used_usd:20, aggregate_remaining_usd:80, detail:'Billing response rejected'}}]);
assert(apiRoot.textContent.includes('Billing response rejected'), 'rejected billing is visible');
assert(!apiRoot.textContent.includes('$80.00 remaining'), 'rejected aggregate cannot masquerade as a balance');
assert(!all(apiRoot).some((e) => e.getAttribute('role') === 'progressbar'), 'rejected balance has no valid-looking bar');
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
assertEqual(all(registry.get('codex-brand')).filter((e) => e.classList.contains('brandmark')).length, 1,
  'OpenAI logo renders once in its section heading');
assertEqual(all(registry.get('grok-brand')).filter((e) => e.classList.contains('brandmark')).length, 1,
  'Grok logo renders once in its section heading');
assert(!all(codexRoot).some((e) => e.classList.contains('brandmark') || e.classList.contains('brandtile')),
  'account rows do not repeat provider logos');
assertEqual(all(codexRows[0]).find((e) => e.classList.contains('plan-label')).textContent, 'Pro Lite',
  'confirmed plan is attached to its own account');
assert(!all(codexRows[1]).some((e) => e.classList.contains('plan-label')), 'unknown plan is omitted');
for (const [raw, label] of [['prolite', 'Pro Lite'], ['plus', 'Plus'], ['pro', 'Pro'], ['pro500', 'Pro500'],
  ['unknown', null], [null, null], ['toString', null]]) {
  const q = {quotas: [{...QUOTAS.quotas[0], plan_type: raw}]};
  renderQuotas(q, MODELS);
  const labels = all(codexRoot).filter(e => e.classList.contains('plan-label'));
  assertEqual(labels.length, label ? 1 : 0, 'only explicit plan enums render');
  if (label) assertEqual(labels[0].textContent, label, 'exact plan display, no inferred Pro500');
}
renderQuotas({quotas: [{...QUOTAS.quotas[0], plan_type:'plus'}, {...QUOTAS.quotas[0], plan_type:'pro'}]}, MODELS);
assert(!all(codexRoot).some(e => e.classList.contains('plan-label')), 'conflicting account plans are not guessed');
renderQuotas(QUOTAS, MODELS);
assert(codexRows.every((row) => !all(row).some((e) => e.classList.contains('pname'))),
  'account headers do not repeat the enclosing Codex heading');
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
assert(grokFill.classList.contains('full'), 'Grok at 100% uses the distinct exhausted burgundy family');
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
assertEqual(all(grokCard).filter((e) => e.classList.contains('brandmark') || e.classList.contains('brandtile')).length, 0,
  'the Grok group does not repeat its section logo');
assertEqual(all(grokCard).filter((e) => e.classList.contains('pname')).length, 0,
  'the Grok group must not duplicate its enclosing section title');
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

for (const [total, level] of [[3, 'ok'], [60, 'warn'], [90, 'danger'], [100, 'full']]) {
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
const exhausted = renderedGrok(100, [{ product: 2, label: 'Grok Build', usage_percent: 97 },
  { product: 4, label: 'Grok Chat', usage_percent: 3 }]);
const nearFull = renderedGrok(81, [{ product: 2, label: 'Grok Build', usage_percent: 78 },
  { product: 4, label: 'Grok Chat', usage_percent: 3 }]);
assertEqual(exhausted.fill.style.width, '100%', 'exhausted styling never changes the percentage geometry');
assertEqual(exhausted.segments.map((s) => s.style.width).join(','), '97%,3%', 'exhausted product math is unchanged');
assert(exhausted.segments.every((s, i) => s.style.backgroundColor !== nearFull.segments[i].style.backgroundColor),
  'exhausted products differ from near-full reds');
assert(!nearFull.fill.classList.contains('full') && !renderedGrok(0, null).fill.classList.contains('full'),
  'near-full and zero do not get the exhausted class');
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
const firstModelCell = registry.get('models').children[0].children[1];
const firstParts = all(firstModelCell).filter((e) => e.classList.contains('part'));
assert(firstParts.length >= 2, 'a merged row breaks its summed USD down per source');
assert(firstParts.every((e) => e.textContent.includes(' · ')),
  'each breakdown line is a source line');
assert(firstParts.some((e) => e.textContent.includes(' tok · ')),
  'agent lines carry their own token count');
const lastPart = firstParts[firstParts.length - 1].textContent;
assert(!/NordRouter .* tok ·/.test(lastPart),
  'a NordRouter line never claims a countable token figure');
assert(registry.get('models-note').textContent.includes('direct agent usage once'),
  'table note explains why the account figure is not summed with the agents');
assert(registry.get('week').textContent.includes('z-ai/glm-5.3'), 'selected top models use direct model window');
const chart = registry.get('history');
let segments = all(chart).filter((e) => e.tagName === 'RECT');
assertEqual(segments.length, 2, 'conflicting account observations cannot acquire an invented model split or gray pool');
assert(segments.every((e) => segmentLabel(e).includes('day known subtotal 16')), 'withhold contradictory NR split, retain independent measurements');
assertEqual(historySeries(MODELS).ledger.get('2026-10-03').unknown, 20, 'the full conflicting account count stays explicitly unresolved');
assertEqual(all(chart).filter((e) => e.tagName === 'SVG').length, 1, 'exactly one chart');
assert(!registry.has('account-history'), 'no second chart container');
assertEqual(modelSwatch('grok-test', 'xai').style.background, chartColor('grok-test'), 'table swatch follows measured chart');
const initialGrokColor = chartColor('grok-test');
renderHistory({ ...MODELS, models: [...MODELS.models].reverse() });
assertEqual(chartColor('grok-test'), initialGrokColor, 'sort order does not recolor models');
windowListeners.resize();
assertEqual(all(chart).filter((e) => e.tagName === 'RECT').length, 2, 'resize preserves counts');
assertEqual(historyScale, 'linear', 'linear scale is default');
registry.get('history-scale').listeners.click[0]();
assertEqual(historyScale, 'balanced', 'balanced toggle works');
assert(all(chart).find((e) => e.tagName === 'SVG').getAttribute('aria-label').includes('not a token axis'), 'balanced axis is not token axis');
assertEqual(chartColor('grok-test'), initialGrokColor, 'scale preserves model color');
registry.get('history-scale').listeners.click[0]();
assertEqual(historyScale, 'linear', 'restore linear');
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
assert(segments.every((e) => segmentLabel(e).includes('day known subtotal 1.0M')),
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
assertEqual(segmentLabel(sol), 'gpt-6.1-sol · 51.7M tokens · day known subtotal 52.7M',
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
assert(registry.get('hero').textContent.includes('loading sources'), 'missing source is not zero');
assertEqual(all(registry.get('hero')).filter((e) => e.classList.contains('pill')).length, 0,
  'incomplete overview must not show numeric source chips');
selectedDays = 30;

const knownZeroCategories = tableModels({models:[{source:'openclaw',provider:'local',model:'zero-cache',
  totals:{input:1,output:1,cache_read:0,cache_write:0,total:2},daily:[]}]})[0];
assertEqual(knownZeroCategories.totals.cache_read,0,'reported zero cache count remains a real zero');
const partialCategories=tableModels({models:[{source:'openclaw',provider:'local',model:'partial',totals:{input:1,total:1},daily:[]},
  {source:'hermes',provider:'local',model:'partial',totals:{total:2},daily:[]}]})[0];
assertEqual(partialCategories.totals.input,null,'one missing contributor cannot become a complete category subtotal');
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
const rowFor = (name) => registry.get('models').children.find((row) =>
  all(row.children[1]).some((e) => e.classList.contains('model-name') && e.textContent === name));
const matchedRow = rowFor('z-ai/glm-5.3');
assertEqual(routeName(matchedRow), 'NordRouter', 'direct model source has its original label');
assertEqual(routeName(rowFor('unmatched-route')), 'nordrouter', 'unmatched route source is unchanged');
assertEqual(routeName(rowFor('other')), 'xai', 'other provider source is unchanged');
const struck = all(matchedRow.children[6]).filter((e) => e.tagName === 'S');
const billedParts = all(matchedRow.children[1]).filter((e) => e.classList.contains('part'));
assertEqual(billedParts.length, 3, 'every reporting source gets its own line');
assertEqual(billedParts[billedParts.length - 1].textContent,
  'NordRouter · z-ai/glm-5.3 · unknown · $2.19 billed',
  'the account figure is listed last and claims no token count: it already includes the agents');
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
assertEqual(directOnly.children[2].textContent, '—', 'account-only input category is unknown, not zero');
assertEqual(directOnly.children[4].textContent, '—', 'account-only cache category is unknown, not zero');
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
const flashRow = registry.get('models').children.find((row) => row.children[1].children[0].textContent.startsWith('deepseek/deepseek-v4.1-flash'));
assertEqual(flashRow.children[1].children[0].textContent, 'deepseek/deepseek-v4.1-flash',
  'the most common spelling labels the merged row');
assertEqual(flashRow.children[6].textContent, '30150', 'both billed spellings are summed before the crossed-out agent total');
assertEqual(flashRow.children[7].textContent, '$1.50billed', 'billed cost of both spellings is summed');
const solRow = registry.get('models').children.find((row) => row.children[1].children[0].textContent === 'gpt-6-sol');
assertEqual(solRow.children[6].textContent, '10', 'agent rows of one model are summed');
assertEqual(solRow.children[7].textContent, '$0.10partial',
  'missing Hermes price stays unknown and cannot borrow an effective rate');
const m3Row = registry.get('models').children.find((row) => row.children[1].children[0].textContent === 'minimax/minimax-m3');
assertEqual(m3Row.children[1].children[0].textContent, 'minimax/minimax-m3',
  'the most common spelling labels the row even when a bare alias is shorter');
assertEqual(m3Row.children[6].textContent, '4160',
  'every spelling of one model lands in a single row: crossed-out agent total, then direct');

/* Labels drop routing and context suffixes but keep the vendor prefix. */
renderModels({ models: [
  { source: 'nordrouter', provider: 'nordrouter', model: 'deepseek/deepseek-v4-flash:saga',
    model_key: 'deepseek-v4-flash', totals: { total: 10 }, cost_usd: 1, daily: [] },
  { source: 'hermes', provider: 'openai', model: 'gpt-6-sol-900k',
    model_key: 'gpt-6-sol', totals: { total: 5 }, cost_usd: null, daily: [] },
]});
assertEqual(registry.get('models').children.map((row) => row.children[1].children[0].textContent).join(','),
  'deepseek/deepseek-v4-flash,gpt-6-sol',
  'routing and context suffixes never leak into the model label');

/* Source-by-source loading preserves server prices and coverage without repricing. */
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
assert(partialHero.includes('Estimated cost') && partialHero.includes('$9.00'),
  'hero uses server estimates without borrowing another source rate');
assert(partialHero.includes('1 models with unknown pricing'),
  'unknown Hermes coverage remains visible');

const SORT_MODELS = { ...MODELS, models: [
  { source: 'openclaw', provider: 'xai', model: 'Alpha', account: 'unknown', cost_usd: 1,
    totals: { total: 10, input: 8, output: 1, cache_read: 1, cache_write: 0 }, daily: [] },
  { source: 'openclaw', provider: 'anthropic', model: 'Zulu', cost_usd: 3,
    totals: { total: 30, input: 4, output: 20, cache_read: 5, cache_write: 1 }, daily: [] },
  { source: 'openclaw', provider: 'xai', model: 'Beta', cost_usd: null,
    totals: { total: 20, input: 12, output: 3, cache_read: 4, cache_write: 1 }, daily: [] },
] };
const modelOrder = () => registry.get('models').children.map((row) => row.children[1].children[0].textContent).join(',');
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

// Router bucket USD and drilldowns are observations, never account allocations.
const ROUTER_FIXTURE = {
  source: 'omniroute', provider: 'omniroute', model: 'nr-bucket', model_key: 'omniroute/nr-bucket',
  totals: { total: 1000 }, cost_usd: 0.75, cost_kind: 'estimated',
  daily: [{ date: '2026-10-23', total: 600, cost_usd: 0.25 }, { date: '2026-10-22', total: 400, cost_usd: 0.5 }],
  bucket_usage: { known_cost_usd: 0.75, status: 'ok', retention_limited: true, routes: [
    { provider: 'nr-route', usage_provenance: 'reported', model: 'vendor/a:fjord', route_path: 'parent/ref:child/0', totals: { total: 400 }, cost_usd: 0.125,
      daily: [{ date: '2026-10-23', totals: { total: 400 }, cost_usd: 0.125 }] },
    { provider: 'nr-route', usage_provenance: 'reported', model: 'vendor/a:fjord', route_path: 'parent/ref:child/0@execution-uuid', totals: { total: 200 }, cost_usd: 0.125,
      daily: [{ date: '2026-10-23', totals: { total: 200 }, cost_usd: 0.125 }] },
    { provider: 'nr-route', usage_provenance: 'reported', model: 'vendor/a:saga', totals: { total: 400 }, cost_usd: 0.5,
      daily: [{ date: '2026-10-22', totals: { total: 400 }, cost_usd: 0.5 }] },
  ] },
};
const bucketAgent = { source: 'openclaw', provider: 'omniroute', model: 'nr-bucket', model_key: 'omniroute/nr-bucket',
  bucket_comparison: true, overview_cost_usd: 0, cost_usd: null, totals: { total: 99 },
  daily: [{ date: '2026-10-23', total: 99 }] };
const mixedBucketRows = [bucketAgent, ROUTER_FIXTURE,
  { source: 'nordrouter', provider: 'nordrouter', model: 'vendor/a:fjord', model_key: 'a', totals: { total: 5000 }, cost_usd: 3, daily: [] }];
const bucketTable = tableModels({ models: mixedBucketRows });
const parentBucket = bucketTable.find((row) => row.bucket);
assertEqual(parentBucket.totals.total, 99, 'router counts do not replace agent bucket tokens');
assertEqual(parentBucket.cost_usd, 0.75, 'parent USD equals its serving route USD, not whole account USD');
assertEqual(parentBucket.token_scope, 'agent-reported', 'agent token provenance is explicit');
assertEqual(bucketTable.find((row) => row.source === 'nordrouter').totals.total, 5000, 'mixed direct NR account row stays separate');
assertEqual(effectiveCost(bucketAgent), null, 'comparison bucket never adds router-wide dollars');
assertEqual(partialModels({ days: 7, parts: { openclaw: { meta: {}, sources: {
  openclaw: { estimated_cost_usd: null, unpriced_models: 1 } }, models: [bucketAgent] }
} }).sources.openclaw.estimated_cost_usd, null, 'server unknown is not replaced with a placeholder zero');
const nordMetricsBefore = registry.get('metrics').textContent;
renderModels({ models: mixedBucketRows });
const bucketRendered = registry.get('models').children.find((row) => row.textContent.includes('nr-bucket'));
assert(bucketRendered.textContent.includes('$0.75') && bucketRendered.textContent.includes('router-wide estimated'), 'bucket parent USD is rendered with scope');
const bucketDisclosure = all(bucketRendered.children[1]).find((item) => item.tagName === 'DETAILS');
const detailRows = registry.get('models').children.filter((row) => row.classList.contains('bucket-detail-row'));
assertEqual(bucketDisclosure.children[0].textContent, '2 serving models', 'bucket control contains only the model count');
assertEqual(bucketDisclosure.children[0].getAttribute('aria-label'), 'nr-bucket: serving models', 'disclosure has a bucket-specific accessible name');
assert(detailRows.every((row) => row.hidden), 'model and day rows start collapsed');
bucketDisclosure.open = true;
bucketDisclosure.listeners.toggle[0]();
assert(detailRows.filter((row) => !row.classList.contains('bucket-day-route')).every((row) => !row.hidden),
  'opening the bucket reveals model and day rows');
assert(detailRows.filter((row) => row.classList.contains('bucket-day-route')).every((row) => row.hidden),
  'day models remain collapsed independently');
const servingRows = detailRows.filter((row) => row.children[1].textContent === 'vendor/a:fjord');
assertEqual(servingRows.length, 2, 'exact serving model is summed once for the bucket and once for its day');
assert(servingRows.every((row) => row.children[6].textContent === '600' && row.children[7].textContent.includes('$0.25')),
  'different execution paths sum their model tokens and own USD in the numeric columns');
assert(detailRows.some((row) => row.children[1].textContent === 'vendor/a:saga'), 'serving IDs keep exact routing suffixes');
const bucketRows = [bucketRendered, ...detailRows];
assert(bucketRows.every((row) => !/Router-wide observation;|Non-additive to overview|Current tariffs, not historical invoices|retained history; gaps unknown/.test(row.textContent)),
  'removed explanatory boilerplate stays absent from every bucket and day column');
assert(bucketRows.every((row) => !/\$|USD|estimate|tariffs|invoices|allocation|Non-additive|gaps unknown/i.test(row.children[1].textContent)),
  'all parent, model and day labels are free of currency, cost scope and explanatory boilerplate');
assert(bucketRows.every((row) => !row.textContent.includes('parent/ref:child/0')), 'execution path UUIDs stay out of the model drilldown');
assert(all(bucketRendered).some((item) => item.tagName === 'SUMMARY' && item.textContent.includes('2 serving models')),
  'serving count represents exact models rather than execution paths');
const dayRow = detailRows.find((row) => row.children[1].textContent.includes('23.10.2026'));
const dayDetail = dayRow.children[1].children[0];
assert(!dayDetail.children[0].textContent.includes('$'), 'day control never repeats its USD');
assert(dayRow.children[7].textContent.includes('$0.25'), 'day USD remains in the right-hand column');
dayDetail.open = true;
dayDetail.listeners.toggle[0]();
const visibleDayModels = detailRows.filter((row) => row.classList.contains('bucket-day-route') && !row.hidden);
assertEqual(visibleDayModels.length, 1, 'day drilldown includes only that day\'s serving model');
assertEqual(visibleDayModels[0].children[1].textContent, 'vendor/a:fjord', 'day model identity is exact');
assertEqual(visibleDayModels[0].children[6].textContent, '600', 'day model tokens are preserved');
assert(visibleDayModels[0].children[7].textContent.includes('$0.25'), 'day model own USD remains on the right');
bucketDisclosure.open = false;
bucketDisclosure.listeners.toggle[0]();
assert(detailRows.every((row) => row.hidden), 'closing a bucket also hides its open day models');
assert(bucketRendered.textContent.includes('retained history only'), 'retention coverage is visible on the parent row');
assertEqual(registry.get('metrics').textContent, nordMetricsBefore, 'NordRouter section remains unchanged');
renderModels({ models: [bucketAgent, { ...ROUTER_FIXTURE, cost_usd: null }] });
const partialBucket = registry.get('models').children.find((row) => row.classList.contains('bucket-row'));
assert(!/\$|USD|unknown|estimate/.test(partialBucket.children[1].textContent), 'unknown and partial USD never leak into the left label');
assert(partialBucket.children[7].textContent.includes('known ≥ $0.75'), 'known subtotal stays in the right USD cell');
renderModels({ models: [{ ...ROUTER_FIXTURE, cost_usd: 0 }] });
const zeroBucket = registry.get('models').children.find((row) => row.classList.contains('bucket-row'));
assert(zeroBucket.children[7].textContent.startsWith('$0.00'), 'a real zero price remains visible on the right');
const missingBucket = tableModels({ models: [bucketAgent] })[0];
assert(missingBucket.cost_usd === null, 'Gateway zero cost table is not free bucket usage');
renderModels({ models: [bucketAgent] });
assert(registry.get('models').textContent.includes('router usage unavailable'), 'missing router does not fabricate a bucket price');

/* NordRouter bucket overlap is removed from the direct family, not from the bucket. */
historyScale = 'linear';
const reportedDirect = [
  { source: 'nordrouter', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash:fjord',
    model_key: 'deepseek-v4.1-flash', totals: { total: 743977604 }, cost_usd: 5.660827, daily: [] },
  { source: 'nordrouter', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash',
    model_key: 'deepseek-v4.1-flash', totals: { total: 45870232 }, cost_usd: 1.327571, daily: [] },
  { source: 'openclaw', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash',
    model_key: 'deepseek-v4.1-flash', totals: { total: 36500000 }, daily: [{ date: '2026-11-01', total: 36500000 }] },
  { source: 'openclaw', provider: 'omniroute', model: 'nr-deepseek-41f', model_key: 'omniroute/nr-deepseek-41f',
    bucket_comparison: true, totals: { total: 651600000 }, daily: [{ date: '2026-11-01', total: 651600000 }] },
  { source: 'omniroute', provider: 'omniroute', model: 'nr-deepseek-41f', model_key: 'omniroute/nr-deepseek-41f',
    totals: { total: 671329015 }, cost_usd: 7.71, cost_kind: 'estimated',
    daily: [{ date: '2026-11-01', total: 671329015 }],
    bucket_usage: { retention_limited: false, routes: [
      { model: 'deepseek-v4.1-flash:fjord', nordrouter: true, totals: { total: 669937892 },
        daily: [{ date: '2026-11-01', totals: { total: 669937892 } }] },
      { model: 'deepseek-v4.1-flash-1017', nordrouter: true, totals: { total: 1391123 },
        daily: [{ date: '2026-11-01', totals: { total: 1391123 } }] },
    ] } },
];
const reportedPayload = {
  meta: { omniroute_status: 'ok' },
  totals: { total: 999 },
  sources: { nordrouter: { tokens: 789847836, billed_cost_usd: 6.988398, complete: true },
    openclaw: { tokens: 1, complete: true }, hermes: { tokens: 0, complete: true } },
  source_daily: { nordrouter: [{ date: '2026-11-01', tokens: 789847836 }] },
  models: reportedDirect,
};
const reportedTable = tableModels(reportedPayload);
const family = reportedTable.find((row) => row.model_key === 'deepseek-v4.1-flash');
const reportedBucket = reportedTable.find((row) => row.bucket);
assertEqual(family.totals.total, 119909944, 'direct family keeps only residual outside proven bucket overlap');
assertEqual(family.comparison_total, 36500000, 'crossed-out comparison stays the non-bucket agent count');
assertEqual(family.cost_usd, null, 'residual does not invent a partitioned invoice');
assert(Math.abs(family.account_billed_cost_usd - 6.988398) < 1e-9, 'account billed dollars stay distinguishable');
assertEqual(family.overlap_unknown_tokens, 1391123, 'dated serving alias is not deducted');
assertEqual(reportedBucket.totals.total, 651600000, 'bucket agent tokens are not replaced or reduced');
assertEqual(reportedBucket.cost_usd, 7.71, 'bucket keeps its router-wide estimate');
renderHero(reportedPayload);
assert(registry.get('hero').textContent.includes('$6.99') || registry.get('hero').textContent.includes('$6.9884'),
  'hero billed cost remains the account invoice');
renderModels(reportedPayload);
const familyRow = registry.get('models').children.find((row) => row.textContent.includes('deepseek-v4.1-flash') && !row.classList.contains('bucket-row') && !row.classList.contains('bucket-detail-row'));
assert(familyRow.textContent.includes('119.9M'), 'table shows the residual, not 789.8M, as the direct figure');
assert(!familyRow.children[6].textContent.includes('789.8M'), 'token cell is not the full account amount');
assert(familyRow.children[6].textContent.includes('36.5M'), 'agent comparison remains visible');
assert(familyRow.children[7].textContent.startsWith('\u2014'), 'USD cell is not an additive residual price');
assert(!familyRow.children[7].textContent.includes('$6.99'), 'account dollars are not repeated as a second price in the USD cell');
assert(familyRow.textContent.includes('unmapped model alias 1.4M'), 'unknown overlap stays explicit');
assert(familyRow.textContent.includes('account billed unpartitioned'), 'account bill is labeled rather than allocated');
const bucketRow = registry.get('models').children.find((row) => row.classList.contains('bucket-row'));
assert(bucketRow.textContent.includes('$7.71'), 'bucket estimate remains beside the residual');
renderHistory(reportedPayload);
const reportedSegments = all(registry.get('history')).filter((entry) => entry.tagName === 'RECT');
assertEqual(reportedSegments.length, 1, 'legacy route observations without reported provenance cannot prove coverage');
assert(segmentLabel(reportedSegments[0]).startsWith('deepseek/deepseek-v4.1-flash · 36.5M'), 'direct measured model-day remains colored, not inflated to account');
assertEqual(historySeries(reportedPayload).ledger.get('2026-11-01').unknown, 753347836, 'missing reported route history is unresolved, not unmatched');
// Covered window above stays residual + bucket. An incomplete 30-day window must
// not list the residual, the agent part, and the bucket as separate token figures.
const incomplete30 = {
  meta: { omniroute_status: 'ok' },
  source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 2716253133 }] },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash:fjord',
      model_key: 'deepseek-v4.1-flash', totals: { total: 1921235252 }, cost_usd: 12.87, daily: [] },
    { source: 'nordrouter', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash',
      model_key: 'deepseek-v4.1-flash', totals: { total: 244042474 }, cost_usd: 6.74, daily: [] },
    { source: 'openclaw', provider: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash',
      model_key: 'deepseek-v4.1-flash', totals: { input: 74800000, output: 2700000, cache_read: 359200000, total: 436700000 }, daily: [] },
    { source: 'openclaw', provider: 'omniroute', model: 'nr-deepseek-41f', model_key: 'omniroute/nr-deepseek-41f',
      bucket_comparison: true, totals: { total: 932500000 }, daily: [] },
    { source: 'omniroute', provider: 'omniroute', model: 'nr-deepseek-41f', model_key: 'omniroute/nr-deepseek-41f',
      totals: { total: 925451748 }, cost_usd: 9.90, cost_kind: 'estimated',
      bucket_usage: { retention_limited: true, routes: [
        { model: 'deepseek-v4.1-flash:fjord', nordrouter: true, totals: { total: 924060625 },
          daily: [{ date: '2026-10-03', totals: { total: 924060625 } }] },
        { model: 'deepseek-v4.1-flash-1017', nordrouter: true, totals: { total: 1391123 },
          daily: [{ date: '2026-10-03', totals: { total: 1391123 } }] },
      ] } },
    { source: 'nordrouter', provider: 'nordrouter', model: 'z-ai/glm-5.3-flash',
      model_key: 'glm-5.3-flash', totals: { total: 311091778 }, cost_usd: 6.29, daily: [] },
    { source: 'nordrouter', provider: 'nordrouter', model: 'z-ai/glm-5.3-flash:fjord',
      model_key: 'glm-5.3-flash', totals: { total: 239883629 }, cost_usd: 2.43, daily: [] },
    { source: 'openclaw', provider: 'nordrouter', model: 'z-ai/glm-5.3-flash',
      model_key: 'glm-5.3-flash', totals: { total: 15000000 }, daily: [] },
    { source: 'openclaw', provider: 'omniroute', model: 'nr-glm-53-flash', model_key: 'omniroute/nr-glm-53-flash',
      bucket_comparison: true, totals: { total: 230500000 }, daily: [] },
    { source: 'omniroute', provider: 'omniroute', model: 'nr-glm-53-flash', model_key: 'omniroute/nr-glm-53-flash',
      totals: { total: 230947922 }, cost_usd: 1.40, cost_kind: 'estimated',
      bucket_usage: { retention_limited: true, routes: [
        { model: 'glm-5.3-flash:fjord', nordrouter: true, totals: { total: 230775027 },
          daily: [{ date: '2026-10-03', totals: { total: 230775027 } }] },
        { model: 'glm-5.3-flash', nordrouter: true, totals: { total: 54011 },
          daily: [{ date: '2026-10-03', totals: { total: 54011 } }] },
      ] } },
  ],
};
const incompleteRows = tableModels(incomplete30);
const deepseek30 = incompleteRows.find((row) => row.model_key === 'deepseek-v4.1-flash');
const glm30 = incompleteRows.find((row) => row.model_key === 'glm-5.3-flash');
const deepseekBucket = incompleteRows.find((row) => row.model === 'nr-deepseek-41f');
const glmBucket = incompleteRows.find((row) => row.model === 'nr-glm-53-flash');
assert(deepseekBucket && deepseekBucket.bucket, '30d still lists nr-deepseek-41f');
assert(glmBucket && glmBucket.bucket, '30d still lists nr-glm-53-flash');
assertEqual(deepseekBucket.totals.total, 932500000, '30d DeepSeek bucket keeps its own token figure');
assertEqual(glmBucket.totals.total, 230500000, '30d GLM bucket keeps its own token figure');
assertEqual(deepseek30.totals.total, 1241217101, '30d DeepSeek NordRouter cell is the exclusive residual');
assertEqual(deepseek30.comparison_total, null, '30d DeepSeek cell has no combined third figure');
assertEqual(deepseek30.totals.total + deepseekBucket.totals.total, 2173717101, '30d DeepSeek residual plus bucket is near the unique account, not account plus bucket');
assert(deepseek30.totals.total + deepseekBucket.totals.total < 2165277726 + 932500000, '30d DeepSeek does not add the full account to the bucket');
assertEqual(glm30.totals.total, 320146369, '30d GLM Flash NordRouter cell is the exclusive residual');
assertEqual(glm30.comparison_total, null, '30d GLM cell has no combined third figure');
assertEqual(glm30.totals.total + glmBucket.totals.total, 550646369, '30d GLM residual plus bucket is the unique total, not 551M plus 230.5M');
renderModels(incomplete30);
const deepseekRow = registry.get('models').children.find((row) => row.textContent.includes('deepseek/deepseek-v4.1-flash') && !row.classList.contains('bucket-row') && !row.classList.contains('bucket-detail-row'));
const glmRow = registry.get('models').children.find((row) => row.textContent.includes('z-ai/glm-5.3-flash') && !row.classList.contains('bucket-row') && !row.classList.contains('bucket-detail-row'));
const deepseekBucketRow = registry.get('models').children.find((row) => row.textContent.includes('nr-deepseek-41f') && row.classList.contains('bucket-row'));
const glmBucketRow = registry.get('models').children.find((row) => row.textContent.includes('nr-glm-53-flash') && row.classList.contains('bucket-row'));
assert(deepseekBucketRow, 'rendered 30d keeps the nr-deepseek-41f name');
assert(glmBucketRow, 'rendered 30d keeps the nr-glm-53-flash name');
assert(deepseekRow.children[6].textContent.includes('1.24B'), '30d DeepSeek token cell is the residual');
assert(!deepseekRow.children[6].textContent.includes('2.17B'), '30d DeepSeek token cell is not the full account beside the bucket');
assert(!deepseekRow.children[6].textContent.includes('436.7M'), '30d DeepSeek token cell does not also show the agent part');
assert(glmRow.children[6].textContent.includes('320.1M'), '30d GLM token cell is the residual');
assert(!glmRow.children[6].textContent.includes('551.0M'), '30d GLM token cell is not the full account beside the bucket');
assert(!glmRow.children[6].textContent.includes('15.0M'), '30d GLM token cell does not also show the agent part');
function intersectionPayload(spec) {
  return {
    meta: { omniroute_status: 'ok' },
    source_daily: { nordrouter: [{ date: '2026-10-03', tokens: spec.account }] },
    models: [
      { source: 'nordrouter', provider: 'nordrouter', model: spec.accountModel, model_key: spec.key,
        totals: { total: spec.account }, cost_usd: 1, daily: [] },
      { source: 'openclaw', provider: 'nordrouter', model: spec.accountModel, model_key: spec.key,
        totals: { total: spec.agent }, daily: [] },
      { source: 'openclaw', provider: 'omniroute', model: spec.bucket, model_key: 'omniroute/' + spec.bucket,
        bucket_comparison: true, totals: { total: spec.bucketTokens }, daily: [] },
      { source: 'omniroute', provider: 'omniroute', model: spec.bucket, model_key: 'omniroute/' + spec.bucket,
        totals: { total: spec.bucketTokens }, cost_usd: 0.4,
        bucket_usage: { retention_limited: spec.limited, routes: [
          { model: spec.serving, nordrouter: true, totals: { total: spec.proven },
            daily: [{ date: '2026-10-03', totals: { total: spec.proven } }] },
        ] } },
    ],
  };
}
function assertIncompleteIntersection(label, spec) {
  const rows = tableModels(intersectionPayload(Object.assign({ limited: true }, spec)));
  const account = rows.find((row) => row.source === 'nordrouter' && row.model_key === spec.key);
  const bucket = rows.find((row) => row.model === spec.bucket);
  assert(bucket && bucket.bucket, label + ' incomplete window still lists the omniroute bucket');
  assertEqual(bucket.totals.total, spec.bucketTokens, label + ' bucket tokens stay full size');
  assertEqual(account.listing, 'residual', label + ' NordRouter cell is the exclusive residual');
  assertEqual(account.totals.total, spec.account - spec.proven, label + ' NordRouter cell is not the full account');
  assertEqual(account.comparison_total, null, label + ' cell is not residual plus combined');
  assertEqual(account.totals.total + bucket.totals.total, spec.account - spec.proven + spec.bucketTokens, label + ' residual plus bucket is not account plus bucket');
  assertEqual(rows.filter((row) => row.model === spec.bucket).length, 1, label + ' bucket name appears once');
}
function assertCoveredIntersection(label, spec) {
  const rows = tableModels(intersectionPayload(Object.assign({ limited: false }, spec)));
  const account = rows.find((row) => row.source === 'nordrouter' && row.model_key === spec.key);
  const bucket = rows.find((row) => row.model === spec.bucket);
  assert(bucket && bucket.bucket, label + ' covered window keeps the bucket row');
  assertEqual(account.listing, null, label + ' covered window is not collapsed to the account total');
  assertEqual(account.totals.total, spec.account - spec.proven, label + ' covered window keeps the residual');
  assertEqual(account.comparison_total, spec.agent, label + ' covered window keeps the agent comparison');
  assertEqual(bucket.totals.total, spec.bucketTokens, label + ' covered bucket tokens stay');
}
const genericFamily = {
  key: 'widget', accountModel: 'vendor/widget', serving: 'widget', bucket: 'nr-widget',
  account: 1000, proven: 400, agent: 250, bucketTokens: 400,
};
const qwenFamily = {
  key: 'qwen3.8-max', accountModel: 'qwen/qwen3.8-max', serving: 'qwen3.8-max', bucket: 'nr-qwen',
  account: 5000000, proven: 2000000, agent: 800000, bucketTokens: 2000000,
};
const mimoFamily = {
  key: 'mimo-v2.5-pro', accountModel: 'xiaomi/mimo-v2.5-pro', serving: 'mimo-v2.5-pro', bucket: 'nr-mimo',
  account: 8000000, proven: 3000000, agent: 900000, bucketTokens: 3000000,
};
[genericFamily, qwenFamily, mimoFamily].forEach((spec) => {
  assertIncompleteIntersection(spec.bucket, spec);
  assertCoveredIntersection(spec.bucket, spec);
});
const mixedFamilies = {
  meta: { omniroute_status: 'ok' },
  source_daily: { nordrouter: [{ date: '2026-10-03', tokens: 14000000 }] },
  models: [].concat(
    intersectionPayload(Object.assign({ limited: true }, qwenFamily)).models,
    intersectionPayload(Object.assign({ limited: true }, mimoFamily)).models,
    [{ source: 'omniroute', provider: 'omniroute', model: 'nr-unmatched', model_key: 'omniroute/nr-unmatched',
      totals: { total: 77 }, bucket_usage: { retention_limited: true, routes: [
        { model: 'not-on-the-account', nordrouter: false, totals: { total: 77 },
          daily: [{ date: '2026-10-03', totals: { total: 77 } }] },
      ] } }],
  ),
};
const mixedRows = tableModels(mixedFamilies);
['nr-qwen', 'nr-mimo', 'nr-unmatched'].forEach((name) => {
  assert(mixedRows.some((row) => row.model === name && row.bucket), 'every payload bucket stays listed: ' + name);
});
renderModels(mixedFamilies);
const qwenCell = registry.get('models').children.find((row) => row.textContent.includes('qwen/qwen3.8-max') && !row.classList.contains('bucket-row'));
assert(qwenCell.children[6].textContent.includes('3.0M'), 'qwen cell is the exclusive residual');
assert(!qwenCell.children[6].textContent.includes('5.0M'), 'qwen cell is not the full account beside the bucket');
assert(!qwenCell.children[6].textContent.includes('800.0K'), 'qwen cell does not also show the agent part');
assert(registry.get('models').textContent.includes('nr-qwen'), 'rendered qwen bucket name stays');
assert(registry.get('models').textContent.includes('nr-mimo'), 'rendered mimo bucket name stays');
assert(registry.get('models').textContent.includes('nr-unmatched'), 'rendered unmatched bucket stays');
const aliasPayload = {
  meta: { omniroute_status: 'ok' },
  source_daily: { nordrouter: [{ date: '2026-11-01', tokens: 140 }] },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'vendor/a:fjord', model_key: 'a', totals: { total: 100 }, cost_usd: 1.5 },
    { source: 'nordrouter', provider: 'nordrouter', model: 'vendor/a', model_key: 'a', totals: { total: 40 }, cost_usd: 0.5 },
    { source: 'omniroute', provider: 'omniroute', model: 'one', model_key: 'omniroute/one', totals: { total: 30 }, cost_usd: 0.2,
      bucket_usage: { routes: [{ model: 'a:fjord', nordrouter: true, totals: { total: 30 }, daily: [{ date: '2026-11-01', totals: { total: 30 } }] }] } },
  ],
};
assertEqual(tableModels(aliasPayload).find((row) => row.model_key === 'a').totals.total, 110,
  'base is not reduced by a :fjord serving match');
const multiPayload = {
  meta: { omniroute_status: 'ok' },
  source_daily: { nordrouter: [{ date: '2026-11-01', tokens: 100 }] },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'vendor/a', model_key: 'a', totals: { total: 100 }, cost_usd: 2 },
    { source: 'omniroute', provider: 'omniroute', model: 'one', model_key: 'omniroute/one', totals: { total: 60 },
      bucket_usage: { routes: [{ model: 'a', nordrouter: true, totals: { total: 60 }, daily: [{ date: '2026-11-01', totals: { total: 60 } }] }] } },
    { source: 'omniroute', provider: 'omniroute', model: 'two', model_key: 'omniroute/two', totals: { total: 50 },
      bucket_usage: { routes: [{ model: 'a', nordrouter: true, totals: { total: 50 }, daily: [{ date: '2026-11-01', totals: { total: 50 } }] }] } },
  ],
};
const multi = tableModels(multiPayload).find((row) => row.source === 'nordrouter');
assertEqual(multi.totals.total, 0, 'two buckets deduct the account model once');
assertEqual(multi.overlap_unreconciled_tokens, 10, 'excess overlap stays explicit');
assertEqual(tableModels(multiPayload).filter((row) => row.bucket).reduce((sum, row) => sum + row.bucket_usage.routes[0].totals.total, 0), 110,
  'neither bucket is reduced to hide the excess');
const otherWindow = {
  meta: { omniroute_status: 'ok' },
  source_daily: { nordrouter: [{ date: '2026-11-01', tokens: 100 }] },
  models: [
    { source: 'nordrouter', provider: 'nordrouter', model: 'vendor/a', model_key: 'a', totals: { total: 100 }, cost_usd: 1 },
    { source: 'omniroute', provider: 'omniroute', model: 'one', model_key: 'omniroute/one', totals: { total: 40 },
      bucket_usage: { routes: [{ model: 'a', nordrouter: true, totals: { total: 40 }, daily: [{ date: '2026-10-01', totals: { total: 40 } }] }] } },
  ],
};
assertEqual(tableModels(otherWindow).find((row) => row.source === 'nordrouter').totals.total, 100,
  'a bucket day outside the account window is not deducted');

function chartTotal(history, date) { return Array.from((history.byDate.get(date) || new Map()).values()).reduce((s,n)=>s+n,0); }
function windowFixture(days) {
  const dates=Array.from({length:days},(_,i)=>new Date(Date.UTC(2026,9,31-days+i)).toISOString().slice(0,10));
  const route=(model,nordrouter,total)=>({provider:'route',model,nordrouter,usage_provenance:'reported',
    totals:{total:total*days},reported_totals:{total:total*days},daily:dates.map(date=>({date,totals:{total},reported_totals:{total}}))});
  return {meta:{days,start_date:dates[0],end_date:dates.at(-1),omniroute_status:'ok',nordrouter_status:'ok'},
    sources:{openclaw:{complete:true},hermes:{complete:true},nordrouter:{complete:true},omniroute:{history_complete:true}},
    source_daily:{nordrouter:dates.map(date=>({date,tokens:1000}))},models:[
      {source:'nordrouter',provider:'nordrouter',model:'vendor/a',totals:{total:1000*days}},
      {source:'openclaw',provider:'nordrouter',model:'vendor/a',totals:{total:300*days},daily:dates.map(date=>({date,total:300}))},
      {source:'openclaw',provider:'xai',model:'independent',totals:{total:5*days},daily:dates.map(date=>({date,total:5}))},
      {source:'hermes',provider:'xai',model:'independent',totals:{total:2*days},daily:dates.map(date=>({date,total:2}))},
      {source:'openclaw',provider:'omniroute',model:'bucket',model_key:'omniroute/bucket',bucket_comparison:true,
        totals:{total:590*days},daily:dates.map(date=>({date,total:590}))},
      {source:'omniroute',provider:'omniroute',model:'bucket',model_key:'omniroute/bucket',totals:{total:610*days},
        bucket_usage:{history_complete:true,retention_limited:false,routes:[route('a',true,600),route('other',false,10)]}},
    ]};
}
for(const days of [1,7,30]) {
 const fixture=windowFixture(days),before=JSON.stringify(fixture),date=fixture.meta.end_date;
 renderHistory(fixture);renderModels(fixture);
 const h=historySeries(fixture),d=h.ledger.get(date);
 assertEqual(d.account,1000,days+'d preserves source account');
 assertEqual(d.covered_unique,900,days+'d router plus direct agents cover only measured tokens');
 assertEqual(d.residual,100,days+'d gray is only certified unmatched difference');
 assertEqual(d.unknown,null,days+'d certified coverage has no unknown remainder');
 assertEqual(chartTotal(h,date),1017,days+'d sums NR once plus disjoint scopes');
 assertEqual(h.byDate.get(date).get('direct-nordrouter-unattributed'),100,days+'d never turns the whole account gray');
 assertEqual(h.byDate.get(date).get(routerHistoryKey(fixture.models.at(-1).bucket_usage.routes[0])),600,days+'d preserves serving model colors/counts');
 assertEqual(h.rowTotals.get('omniroute/bucket'),610*days,days+'d bucket chart count equals its reported serving routes');
 assertEqual(tableModels(fixture).find(r=>r.bucket).history_total,610*days,days+'d table identifies the exact chart count separately from agent comparison');
 assertEqual(all(chart).filter(e=>e.tagName==='SVG').length,1,days+'d has one chart');
 assertEqual(JSON.stringify(fixture),before,days+'d reconciliation never mutates snapshot');
}
const partial=windowFixture(30),date=partial.meta.end_date;
partial.sources.omniroute.history_complete=false;partial.models.at(-1).bucket_usage.retention_limited=true;
let h=historySeries(partial),d=h.ledger.get(date);
assertEqual(d.residual,0,'partial retained history cannot establish unmatched traffic');
assertEqual(d.unknown,100,'partial retention leaves numeric remainder unresolved');
assertEqual(chartTotal(h,date),917,'partial history retains all actually measured colors without inventing residual');
assertEqual(h.byDate.get(date).get('direct-nordrouter-unattributed'),undefined,'no gray for unknown coverage');
const estimated=windowFixture(1),rt=estimated.models.at(-1).bucket_usage.routes[0];
rt.usage_provenance='mixed';rt.totals.total=900;rt.reported_totals.total=600;
rt.daily[0].totals.total=900;rt.daily[0].reported_totals.total=600;rt.daily[0].unverified_requests=1;
h=historySeries(estimated);d=h.ledger.get(estimated.meta.end_date);
assertEqual(d.router_reported,600,'estimated prompt sizes are not billed/covered tokens');
assertEqual(d.unverified_router,300,'estimated/error observation is retained in ledger');
assertEqual(d.unknown,100,'uncertain attempt prevents unmatched claim');
assertEqual(d.residual,0,'unverified attempt is not a gray count');
const alias=windowFixture(1);alias.models.at(-1).bucket_usage.routes[0].model='not-on-account-top-list';
h=historySeries(alias);
assertEqual(h.ledger.get(alias.meta.end_date).covered_unique,900,'known account origin establishes day overlap independently of top-model aliases');
assert(h.categories.has(routerHistoryKey(alias.models.at(-1).bucket_usage.routes[0])),'unmatched spelling retains its actually reported serving model label');
const unknownOrigin=windowFixture(1);unknownOrigin.models.at(-1).bucket_usage.routes[0].nordrouter=null;
h=historySeries(unknownOrigin);d=h.ledger.get(unknownOrigin.meta.end_date);
assertEqual(d.covered_unique,300,'unknown origin is not automatically in the account');
assertEqual(d.residual,0,'unknown origin never becomes a certified residual');
assertEqual(d.unknown,700,'unknown origin keeps the full unproved remainder');
const conflict=windowFixture(1);conflict.source_daily.nordrouter[0].tokens=100;
h=historySeries(conflict);d=h.ledger.get(conflict.meta.end_date);
assertEqual(d.covered_unique,0,'conflict cannot be resolved by arbitrary caps or proportional shares');
assertEqual(d.unknown,100,'contradictory account remains unresolved');
assertEqual(chartTotal(h,conflict.meta.end_date),17,'only independent scopes survive contradictory account split');
const missing=windowFixture(1);missing.source_daily.nordrouter=[];
h=historySeries(missing);assertEqual(h.ledger.get(missing.meta.end_date).account,null,'missing account never becomes zero');
assertEqual(chartTotal(h,missing.meta.end_date),917,'account outage retains reported measurements without adding agent bucket comparisons');
const duplicate=windowFixture(1);duplicate.source_daily.nordrouter.push({...duplicate.source_daily.nordrouter[0]});
h=historySeries(duplicate);assertEqual(h.ledger.get(duplicate.meta.end_date).account,null,'duplicate account day is unavailable, never double');
assertEqual(h.ledger.get(duplicate.meta.end_date).residual,0,'duplicate account day cannot establish unmatched residual');
const multiDay=windowFixture(1);multiDay.models[1].source='hermes';multiDay.models[1].daily[0].account_day_compatible=false;
h=historySeries(multiDay);d=h.ledger.get(multiDay.meta.end_date);
assertEqual(d.direct_agents,0,'last-activity session attribution cannot prove account-day usage');
assertEqual(d.incompatible_agent_days,300,'multi-day session count is not silently lost');
assertEqual(d.unknown,400,'multi-day session history is unresolved, not unmatched');
const badDay=windowFixture(1);badDay.models.at(-1).bucket_usage.routes[0].daily[0].reported_totals.total=999;
h=historySeries(badDay);assertEqual(h.ledger.get(badDay.meta.end_date).router_reported,0,'mismatched daily/window route counts cannot be fabricated into a split');
const glm=windowFixture(1),gd=glm.meta.end_date;
glm.source_daily.nordrouter[0].tokens=599295097;
glm.models[1].model='z-ai/glm-5.3';glm.models[1].daily[0].total=7076;glm.models[1].totals.total=7076;
glm.models.at(-1).bucket_usage.routes=[
 {provider:'nr',model:'glm-5.3',nordrouter:true,usage_provenance:'reported',totals:{total:36879450},daily:[{date:gd,totals:{total:36879450}}]},
 {provider:'nr',model:'other-reported-models',nordrouter:true,usage_provenance:'reported',totals:{total:557840054},daily:[{date:gd,totals:{total:557840054}}]},
];glm.sources.omniroute.history_complete=false;
renderHistory(glm);h=historySeries(glm);d=h.ledger.get(gd);
assertEqual(d.covered_unique,594726580,'599.3M regression reconciles actual reported routes plus 7076 direct tokens');
assertEqual(d.unknown,4568517,'599.3M regression exposes only unproved remainder, not a huge gray account');
assertEqual(d.residual,0,'unproved 4.57M is not called unmatched');
assertEqual(h.byDate.get(gd).get(routerHistoryKey(glm.models.at(-1).bucket_usage.routes[0])),36879450,'GLM receives exactly its reported route count, never the whole account');
assert(!all(chart).some(e=>e.tagName==='RECT'&&e.getAttribute('fill')==='#8491a3'),'real regression has no fake-gray account pool');
assert(all(chart).filter(e=>e.tagName==='RECT').every(e=>!segmentLabel(e).includes('glm-5.3 · 599.3M')),'no account-to-GLM allocation');

console.log('OK: dashboard DOM contract holds');
selectedDays = initialSelectedDays;
setPeriodButtons();
if (historyScale !== 'linear') registry.get('history-scale').listeners.click[0]();
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
  const sourceOf = (url) => url.includes('source=omniroute') ? 'omniroute' : url.includes('nordrouter') ? 'nordrouter'
    : (url.includes('source=hermes') ? 'hermes' : 'openclaw');
  const answer = (entry, days) => reply(entry, entry.url.startsWith('/api/v1/quotas')
    ? quotaResponse(entry.url) : modelFor(days, sourceOf(entry.url)));
  // Hermes contributes no tokens in this scenario: the point is that its part
  // must complete before the canonical total is published, not that it changes it.
  const modelFor = (days, source, date = '2026-10-03') => ({
    meta: { ...MODELS.meta, start_date: date, end_date: date, days,
      openclaw_status: source === 'openclaw' ? 'ok' : 'pending',
      hermes_status: source === 'hermes' ? 'ok' : 'pending',
      nordrouter_status: source === 'nordrouter' ? 'ok' : 'pending',
      omniroute_status: source === 'omniroute' ? 'ok' : 'pending' },
    sources: source === 'omniroute' ? { omniroute: { complete: false, additive: false } } : source === 'nordrouter'
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
      : source === 'hermes' || source === 'omniroute' ? []
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
  reply(request(urlFor(7, 'omniroute')), modelFor(7, 'omniroute'));
  reply(request(urlFor(7, 'hermes')), modelFor(7, 'hermes'));
  await settle();
  assert(registry.get('models').textContent.includes('direct-7d'), 'direct model renders before OpenClaw');
  assert(registry.get('history').textContent.includes('NR unresolved 7'), 'account-only arrival is unresolved, never a gray allocation');
  assert(registry.get('hero').textContent.includes('loading sources'), 'an account-only unresolved balance is not a fabricated total');
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
  reply(request(urlFor(1, 'omniroute'), beforeOne), modelFor(1, 'omniroute'));
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
  assert(all(chart).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('day known subtotal 2')),
    'single history joins account plus independent agent tokens, not a second chart');
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
    entry.url === urlFor(7, 'openclaw') + '&refresh=true').length, 1,
  'full refresh does not duplicate a pending Gateway request');
  successful.find((entry) => entry.url === urlFor(7, 'openclaw') + '&refresh=true').resolve({
    ok: true, json: () => Promise.resolve(modelFor(7, 'openclaw')),
  });
  pendingFetches.slice(beforeSuccessfulRefresh).filter((entry) =>
    entry.url.startsWith('/api/v1/quotas') || entry.url === urlFor(7, 'nordrouter') + '&refresh=true' ||
    entry.url === urlFor(7, 'hermes') + '&refresh=true' || entry.url === urlFor(7, 'omniroute') + '&refresh=true')
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
  assert(all(chart).some((e) => e.tagName === 'RECT' && segmentLabel(e).includes('day known subtotal 2')),
    'thirty-day single history joins only disjoint counts');
  assertEqual(historyScale, 'linear', 'progressive loads preserve linear scale default');
  // A failed live Gateway read is served from the last successful saved window
  // with an explicit stale label, the original fetch time, and the failure
  // text; the canonical total stays unavailable instead of pretending to be
  // current, and a later success clears the warning.
  let savedOpenClaw = true;
  const savedStamp = '2026-10-03T03:49:55+00:00';
  const savedError = 'CalledProcessError: command failed with exit status 1';
  const savedModel = 'grok-saved-' + selectedDays + 'd';
  const liveModel = 'grok-' + selectedDays + 'd';
  const openclawBody = (days) => savedOpenClaw
    ? { ...modelFor(days, 'openclaw'),
        meta: { ...modelFor(days, 'openclaw').meta, openclaw_status: 'cached',
          openclaw_cached_at_utc: savedStamp, openclaw_error: savedError },
        sources: { openclaw: { tokens: 2, estimated_cost_usd: 0.03, complete: false },
          openclaw_nordrouter_comparison_tokens: 0 },
        models: [{ source: 'openclaw', provider: 'xai', model: savedModel, cost_usd: 0.03,
          totals: { total: 2 }, daily: [{ date: '2026-10-03', total: 2 }] }] }
    : modelFor(days, 'openclaw');
  global.fetch = (url) => {
    if (url === '/api/v1/display/update') return Promise.resolve({ ok: true, json: () => Promise.resolve(PAYLOAD) });
    if (url.startsWith('/api/v1/quotas')) return Promise.resolve({ ok: true, json: () => Promise.resolve(quotaResponse(url)) });
    return Promise.resolve({ ok: true, json: () => Promise.resolve(
      url.includes('openclaw') ? openclawBody(selectedDays) : modelFor(selectedDays, sourceOf(url))) });
  };
  registry.get('refresh').listeners.click[0]();
  await waitForReload();
  await settle();
  assert(registry.get('models').textContent.includes(savedModel),
    'the saved OpenClaw window is still shown');
  assert(registry.get('error').textContent.includes('OpenClaw: saved unverified snapshot ' + time(savedStamp)),
    'the warning names the original successful fetch time');
  assert(registry.get('error').textContent.includes(savedError),
    'the warning names the live failure');
  assert(registry.get('error').textContent.includes('latest read unavailable'),
    'the saved window is never presented as current');
  assert(registry.get('model-stamp').textContent.includes('OpenClaw cached (saved ' + time(savedStamp) + ')'),
    'the models stamp repeats the saved fetch time');
  assert(registry.get('hero').textContent.includes('known subtotal · incomplete coverage'),
    'saved measured history stays visible without claiming a live complete total');
  assert(!registry.get('hero').textContent.includes('0 tokens'),
    'a stale read is never rendered as zero usage');
  savedOpenClaw = false;
  registry.get('refresh').listeners.click[0]();
  await waitForReload();
  await settle();
  assert(!registry.get('error').textContent.includes('OpenClaw: saved'),
    'a successful live read clears the saved-window warning');
  assert(registry.get('models').textContent.includes(liveModel), 'live OpenClaw rows replace the saved ones');
  assert(!registry.get('models').textContent.includes(savedModel),
    'the saved row is replaced, not duplicated');

  // A transient successful-but-partial response is not a replacement for a
  // complete exact-window snapshot. Its timestamp belongs to the attempted
  // refresh, not to the data that remains on screen.
  const originalStamp = '2026-10-03T10:00:00+00:00';
  const fullPart = modelFor(selectedDays, 'openclaw');
  fullPart.meta = {...fullPart.meta, openclaw_snapshot_complete:true,
    openclaw_source_at_utc:originalStamp, openclaw_status:'ok'};
  fullPart.sources.openclaw.bucket_comparison_tokens = 1;
  lastLoaded.parts.openclaw = fullPart;
  lastLoaded.modelsDirty = true;
  renderAvailable(lastLoaded);
  const retainedState = lastLoaded;
  const retainedHero = registry.get('hero').textContent;
  const originalSetTimeout = global.setTimeout;
  const retryCallbacks = [];
  let sourceReads = 0;
  global.setTimeout = (fn, milliseconds) => milliseconds === 8000
    ? (retryCallbacks.push(fn), 90000 + retryCallbacks.length)
    : originalSetTimeout(fn, milliseconds);
  global.fetch = (url) => {
    if (url.includes('source=openclaw')) {
      sourceReads++;
      return Promise.resolve({ok:true,json:()=>Promise.resolve({...fullPart,
        meta:{...fullPart.meta,openclaw_status:'refreshing',openclaw_snapshot_complete:false,
          openclaw_refreshing:true,openclaw_source_at_utc:'2026-10-03T11:00:00+00:00'},
        models:[],sources:{openclaw:{complete:false,tokens:null}}})});
    }
    if (url.startsWith('/api/v1/quotas')) return Promise.resolve({ok:true,json:()=>Promise.resolve(quotaResponse(url))});
    return Promise.resolve({ok:true,json:()=>Promise.resolve(modelFor(selectedDays,sourceOf(url)))});
  };
  try {
    lastLoaded.openclawRetries = 0;
    loadSources(loadVersion,lastLoaded,['openclaw'],false);
    await settle();
    assertEqual(lastLoaded.parts.openclaw.models[0].model,fullPart.models[0].model,
      'refreshing empty rows cannot wipe a complete model history');
    assertEqual(lastLoaded.parts.openclaw.meta.openclaw_source_at_utc,originalStamp,
      'an incomplete refresh cannot advance the displayed observation timestamp');
    assertEqual(registry.get('hero').textContent,retainedHero,
      'the known total stays stable when a refresh loses coverage');
    while (retryCallbacks.length) {retryCallbacks.shift()(); await settle();}
    assertEqual(sourceReads,8,'revalidation has a finite per-period request budget');
    const otherPeriod = selectedDays === 1 ? 7 : 1;
    clickPeriod(otherPeriod); await settle();
    clickPeriod(retainedState.days);
    assertEqual(lastLoaded,retainedState,'switching back reuses the exact saved period state immediately');
    assertEqual(lastLoaded.parts.openclaw.models[0].model,fullPart.models[0].model,
      'period switching does not blank retained model history');
    await settle();
  } finally {
    global.setTimeout = originalSetTimeout;
    Object.keys(openclawRetryTimers).forEach(key => {clearTimeout(openclawRetryTimers[key]);delete openclawRetryTimers[key];});
  }

  if (process.argv[3]) {
    const actual = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
    const parts = {};
    ['openclaw', 'hermes', 'nordrouter', 'omniroute'].forEach((source) => {
      parts[source] = { ...actual, models: actual.models.filter((m) => m.source === source),
        sources: { [source]: actual.sources[source] } };
    });
    const merged = partialModels({ days: actual.meta.days, parts });
    assertEqual(JSON.stringify(merged.sources.openclaw), JSON.stringify(actual.sources.openclaw),
      'independent-source UI must preserve the exact server estimate and coverage');
    assertEqual(JSON.stringify(merged.sources.hermes), JSON.stringify(actual.sources.hermes),
      'Hermes uses its own category estimate, not a borrowed OpenClaw rate');
    const sum = ['openclaw', 'hermes'].reduce((v, k) => v + (actual.sources[k].estimated_cost_usd || 0), 0);
    renderHero(merged);
    assert(registry.get('hero').textContent.includes(usd(sum)), 'hero renders API agent estimate');
    assert(registry.get('hero').textContent.includes('standard rates, short-context baseline'), 'baseline is explained once');
    const canonical = tableModels(merged).filter((r) => !r.bucket && r.source !== 'nordrouter');
    const tableSum = canonical.reduce((v, r) => v + (r.cost_usd || 0), 0);
    assert(Math.abs(tableSum - sum) < 1e-9, 'model table and hero use identical API dollars');
    renderHistory(merged);
    assert(registry.get('history').textContent.length > 0, 'token history survives API repricing');
    console.log('OK: server/API/hero/model rows agree on category estimates');
  }
  pendingFetches.filter((entry) => entry.url === quotaFor('openai_api')).forEach((entry) => reply(entry, quotaResponse(entry.url)));
  await flush();
  await flush();
  console.log('OK: independent sources render progressively and preserve period/scale state');
})().catch((error) => { console.error(error); process.exitCode = 1; });
