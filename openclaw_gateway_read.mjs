/** Read-only all-agent aggregates through the installed public OpenClaw SDK.
 * Session rows, configuration and authentication never leave this process.
 * Importing this module alone performs no I/O.
 */
import {pathToFileURL} from 'node:url';

const TOTAL_FIELDS = ['input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens', 'totalCost', 'missingCostEntries'];
function iso(ms) { return new Date(ms).toISOString().slice(0, 10); }
function validDate(value) { return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && iso(Date.parse(value)) === value; }
function identity(row) { return JSON.stringify([row.provider, row.model]); }
function count(n) { return Number.isSafeInteger(n) && n >= 0; }
export function narrow(reply, startDate, endDate) {
  if (!reply || reply.startDate !== startDate || reply.endDate !== endDate || !reply.aggregates ||
      !Array.isArray(reply.aggregates.byModel) || !Array.isArray(reply.aggregates.modelDaily)) throw Error('Gateway usage window/shape mismatch');
  const byModel = [], modelDaily = [], models = new Map(), days = new Set(), sums = new Map();
  for (const row of reply.aggregates.byModel) {
    if (typeof row.provider !== 'string' || typeof row.model !== 'string' || !count(row.totals?.totalTokens) || models.has(identity(row))) throw Error('Invalid Gateway model aggregate');
    const totals = {};
    for (const field of TOTAL_FIELDS) if (field in row.totals) {
      const value = row.totals[field];
      if (typeof value !== 'number' || !Number.isFinite(value) || (field !== 'totalCost' && !count(value))) throw Error('Invalid Gateway token counter');
      totals[field] = value;
    }
    byModel.push({provider: row.provider, model: row.model, totals}); models.set(identity(row), row.totals.totalTokens);
  }
  for (const row of reply.aggregates.modelDaily) {
    const key = JSON.stringify([row.provider, row.model, row.date]);
    if (!models.has(identity(row)) || !validDate(row.date) || row.date < startDate || row.date > endDate || !count(row.tokens) || days.has(key)) throw Error('Invalid Gateway model day');
    days.add(key); sums.set(identity(row), (sums.get(identity(row)) || 0) + row.tokens);
    modelDaily.push({provider: row.provider, model: row.model, date: row.date, tokens: row.tokens,
      ...(typeof row.cost === 'number' && Number.isFinite(row.cost) ? {cost: row.cost} : {})});
  }
  const consistent = [...models].every(([key, tokens]) => tokens === (sums.get(key) || 0));
  const cache = reply.cacheStatus;
  const empty = !cache && reply.totals?.totalTokens === 0 && reply.sessions?.length === 0 && byModel.length === 0 && modelDaily.length === 0;
  const complete = consistent && !reply.partial && !cache?.partial &&
    (empty || (cache?.status === 'fresh' && (cache.pendingFiles || 0) === 0 && (cache.staleFiles || 0) === 0));
  return {aggregates: {byModel, modelDaily}, complete, status: complete ? 'fresh' : (cache?.status || 'partial'),
    ...(count(reply.updatedAt) ? {updatedAt: reply.updatedAt} : {}),
    ...(cache ? {cacheStatus: Object.fromEntries(['status','cachedFiles','pendingFiles','staleFiles','refreshedAt'].filter(k => k in cache).map(k => [k, cache[k]]))} : {}),
    ...(reply.partial?.reason || cache?.partial?.reason ? {reason: reply.partial?.reason || cache.partial.reason} : {}),
    ...(!consistent ? {reason: 'model-day-mismatch'} : {})};
}
// Paging never filters aggregate inputs. The Gateway rejects >20,000 usage
// instances; offset 20,000 therefore guarantees an empty session page without
// changing the all-agent population. Do not request projection:"overview": it
// switches the Gateway to committed-only (potentially incomplete) reporting.
export async function collect(call, startDate, endDate) {
  if (!validDate(startDate) || !validDate(endDate) || startDate > endDate) throw Error('Invalid UTC usage window');
  const observedFromUtc = new Date().toISOString(), requests = [];
  async function read() {
    const begin = Date.now();
    const reply = await call('sessions.usage', {agentScope:'all', startDate, endDate,
      mode:'utc', limit:1, offset:20000, includeContextWeight:false});
    if (!Array.isArray(reply.sessions) || reply.sessions.length) throw Error('Gateway returned a nonempty aggregate-only page');
    const checked = narrow(reply, startDate, endDate);
    requests.push({startDate, endDate, milliseconds:Date.now()-begin,
      status:checked.status, complete:checked.complete, ...(checked.reason ? {reason:checked.reason} : {})});
    return {reply, checked};
  }
  let {reply, checked} = await read();
  // One bounded immediate retry can observe the worker publication that a
  // refreshing result requested. Replace the whole response, never union/max.
  if (checked.status === 'refreshing') ({reply, checked} = await read());
  return {startDate, endDate, aggregates:checked.aggregates,
    ...(checked.updatedAt !== undefined ? {updatedAt:checked.updatedAt} : {}),
    cacheStatus:checked.cacheStatus || {status:checked.complete ? 'fresh' : 'partial'},
    ...(!checked.complete ? {partial:{reason:checked.reason || 'upstream-incomplete'}} : {}),
    coverage:{strategy:'all-agents-exact-empty-page-v1', complete:checked.complete,
      observedFromUtc, observedUntilUtc:new Date().toISOString(), requests}};
}
async function main() {
  const [sdk, startDate, endDate] = process.argv.slice(2);
  const {callGatewayFromCli} = await import(pathToFileURL(sdk).href);
  const signal = AbortSignal.timeout(55000);
  const call = (method, params) => callGatewayFromCli(method,{json:true,timeout:'20000'},params,
    {sharedStateMode:'read-only',progress:false,signal});
  process.stdout.write(JSON.stringify(await collect(call,startDate,endDate)));
}
if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href)
  main().catch(()=>{process.stderr.write('OpenClaw read-only aggregate request failed\n');process.exitCode=1;});
