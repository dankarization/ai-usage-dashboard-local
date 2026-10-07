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
['hero', 'quotas', 'history', 'codex', 'metrics', 'today', 'week',
 'model-stamp', 'models', 'stamp', 'error', 'refresh', 'nr-status',
 'est-value', 'est-sub'].forEach((id) => {
  registry.set(id, new Element('div'));
});

global.document = {
  getElementById: (id) => registry.get(id) || null,
  createElement: (tag) => new Element(tag),
  createElementNS: (ns, tag) => new Element(tag, ns),
};
global.window = { addEventListener: () => {} };
let autoRefreshTick;
global.setInterval = (fn) => { autoRefreshTick = fn; return 0; };
global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}) });

/* ---------------- run the real dashboard script ---------------- */
const code = fs.readFileSync(jsPath, 'utf8');
vm.runInThisContext(code, { filename: jsPath });

['renderQuotas', 'renderHero', 'renderHistory', 'renderModels', 'severity', 'statusClass', 'providerName', 'tokens', 'usd', 'countdown', 'groupQuotas', 'effectiveStatus', 'brandMark', 'orderGroups', 'subscriptionEstimate', 'renderEstimate', 'snapshotLabel', 'relativeAge']
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
      next_reset_time_ms: 1791064244000, next_reset_iso: '2026-10-04T01:50:44', usage: null, remaining: null },
    { provider: 'grok_bot', label: 'Weekly Grok Bot Limit', status: 'unavailable',
      used_percentage: null, remaining_percentage: null },
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
  meta: { generated_at: '2026-10-03T03:49:55', start_date: '2026-09-27', end_date: '2026-10-03', days: 7 },
  totals: { total: 535720909 },
  models: [
    { source: 'nordrouter', model: 'z-ai/glm-5.3', cost_usd: 7.822423, totals: { total: 61934392 }, daily: [] },
    { source: 'nordrouter', model: 'deepseek/deepseek-v4.1-flash:fjord', cost_usd: 1.865491, totals: { total: 336041919 }, daily: [] },
    { source: 'glm', model: 'glm-coding-plan', cost_usd: null, totals: { total: 500 }, daily: [] },
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

/* ---------------- quota rendering ---------------- */
renderQuotas(QUOTAS);

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
assertEqual(grokBars.length, 1, 'Grok must render one bar');
const grokFill = all(grokBars[0]).find((e) => e.classList.contains('fill'));
assert(grokFill.classList.contains('danger'), 'Grok at 100% must be colour-coded danger');
assertEqual(grokFill.style.width, '100%', 'Grok fill width');
assert(grokCard.textContent.includes('100% used'), 'Grok must show 100% used');
assert(grokCard.textContent.includes('0% left'), 'Grok must show 0% left');
// The live Grok row carries a real percentage but no explicit status field;
// it must read as Active, not as Unknown.
assert(grokCard.textContent.includes('Active'), 'a window with a real percentage must read as Active');
assert(!grokCard.textContent.includes('Unknown'), 'a live window must not be labelled Unknown');
assertEqual(effectiveStatus({ windows: [{ used_percentage: 42 }] }), 'ok',
  'effectiveStatus falls back to ok when a percentage exists');
assertEqual(effectiveStatus({ windows: [{ used_percentage: null }] }), 'unknown',
  'effectiveStatus stays unknown without a percentage or status');
assertEqual(effectiveStatus({ status: 'not_configured', windows: [{ used_percentage: 42 }] }), 'not_configured',
  'an explicit status wins over the percentage fallback');

assert(!quotaRoot.textContent.includes('NordRouter'), 'NordRouter must not appear in the quota grid');
assertEqual(all(quotaRoot).filter((e) => e.classList.contains('limit-group')).length, 2,
  'the quota list must hold separate Grok and Grok Bot rows');
const botCard = cardFor(quotaRoot, 'Grok Bot')[0];
assert(botCard && botCard.textContent.includes('Unavailable'), 'Grok Bot must be honestly unavailable');
assert(withRole(botCard, 'progressbar').length === 0, 'Grok Bot must never copy the weekly pool percentage');
assertEqual(orderGroups([{ provider: 'glm', account: '' }, { provider: 'grok', account: '' }, { provider: 'codex', account: 'codex_2' }, { provider: 'codex', account: 'codex_1' }])
  .map((g) => g.provider + (g.account || '')).join(','),
  'codexcodex_1,codexcodex_2,grok,glm',
  'quota order must be Codex accounts, then Grok, then the rest');
assert(registry.get('nr-status').textContent.includes('incomplete'),
  'NordRouter partial-today note must be surfaced');
assert(registry.get('metrics').textContent.includes('$17.08'),
  'NordRouter balance must render as USD, got: ' + registry.get('metrics').textContent);
assert(registry.get('today').textContent.includes('z-ai/glm-5.3'), 'top models today must render');

/* ---------------- overview + history ---------------- */
renderHero(PAYLOAD);
const heroText = registry.get('hero').textContent;
assert(heroText.includes('1.03B'), 'hero must show the 30d token total');
assert(heroText.includes('$46.46'), 'hero must show the 30d cost');
assert(heroText.includes('2 active days'), 'hero must count only days with real usage');
assert(heroText.includes('2026-09-06'), 'hero must point at the latest active day');

renderHistory(PAYLOAD);
const history = registry.get('history');
const rects = all(history).filter((e) => e.tagName === 'RECT');
assertEqual(rects.length, 3, 'chart must draw one bar per day in the window, preserving the time axis');
const zeroBar = rects.find((r) => {
  const title = all(r).find((c) => c.tagName === 'TITLE');
  return title && title.textContent.includes('2026-09-05');
});
assert(zeroBar, 'a zero-usage day must still occupy its slot');
assertEqual(zeroBar.getAttribute('height'), '0', 'a zero-usage day must render at height 0, not a fake bar');
const realBar = rects.find((r) => {
  const title = all(r).find((c) => c.tagName === 'TITLE');
  return title && title.textContent.includes('2026-09-06');
});
assert(parseFloat(realBar.getAttribute('height')) > 0, 'a day with usage must render a visible bar');
assert(history.textContent.includes('peak 250.0M'), 'chart legend must report the real peak');
const titled = rects.map((r) => all(r).find((c) => c.tagName === 'TITLE')).filter(Boolean);
assertEqual(titled.length, 3, 'each bar must carry a tooltip with its real value');
assert(titled.some((t) => t.textContent.includes('2026-09-06') && t.textContent.includes('250,000,000')),
  'bar tooltip must expose the real token count');

renderHistory({ daily: [{ date: '2026-10-03', total_tokens: 0, cost_usd: 0 }] });
assert(registry.get('history').textContent.includes('No historical usage data available'),
  'an all-zero history must render an explicit empty state');
assert(all(registry.get('history')).filter((e) => e.tagName === 'RECT').length === 0,
  'an all-zero history must not draw bars');

/* ---------------- model table ---------------- */
renderModels(MODELS);
const rows = all(registry.get('models')).filter((e) => e.tagName === 'TR');
assertEqual(rows.length, 3, 'model table must render one row per model');
assert(rows[0].textContent.includes('z-ai/glm-5.3'), 'rows must be sorted by cost descending');
assert(rows[0].textContent.includes('$7.82'), 'top row must show its real cost');
assert(rows[2].textContent.includes('—'), 'a model with no reported cost must show — not $0');
assert(registry.get('model-stamp').textContent.includes('2026-09-27'), 'model stamp must show the window');

renderModels({ models: [] });
assert(registry.get('models').textContent.includes('No model data available'),
  'an empty model list must render an explicit empty state');

/* ---------------- subscription list-price estimate ---------------- */
// NordRouter cost is actual billed spend and must never be counted as an
// estimate; only subscription sources with measured tokens count.
var est = subscriptionEstimate([
  { source: 'nordrouter', cost_usd: 100.0 },
  { source: 'codex', cost_usd: 12.5 },
  { source: 'glm', cost_usd: null },
]);
assertEqual(est.count, 1, 'only priced subscription sources count toward the estimate');
assertEqual(est.total, 12.5, 'the estimate sums subscription list prices only');
assertEqual(subscriptionEstimate([{ source: 'nordrouter', cost_usd: 5 }]).count, 0,
  'a NordRouter-only dataset yields no subscription estimate');

lastModels = [{ source: 'nordrouter', cost_usd: 46.5 }];
renderEstimate();
assertEqual(registry.get('est-value').textContent, '—',
  'without measured subscription tokens the estimate must read unavailable');
assert(registry.get('est-sub').textContent.includes('unavailable'),
  'the estimate must explain why it is unavailable');
assert(!registry.get('est-value').textContent.includes('0'),
  'a missing estimate must never be rendered as 0');

lastModels = [{ source: 'codex', cost_usd: 12.5 }, { source: 'grok', cost_usd: 7.5 }];
renderEstimate();
assertEqual(registry.get('est-value').textContent, '$20.00', 'a real estimate must render its sum');
assert(registry.get('est-sub').textContent.includes('estimate'),
  'the estimate must be labelled as an estimate');

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
  console.log('OK: accelerated auto-refresh repainted changed provider data');
})().catch((error) => { console.error(error); process.exitCode = 1; });
