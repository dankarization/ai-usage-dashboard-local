// Aggregate-only, read-only query. Never exports credentials or request bodies.
'use strict';
const fs = require('fs');
const DB = require('better-sqlite3');
const {end} = JSON.parse(fs.readFileSync(0, 'utf8'));
if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$/.test(end)) throw Error('Invalid end');
const db = new DB('/app/data/storage.sqlite', {readonly: true, fileMustExist: true});
db.pragma('query_only = ON');
const snapshot = db.transaction(() => {
  // Exact connection identity, not model name or shared provider alias. Current
  // metadata is not proof that a connection's endpoint never changed historically.
  const ids = db.prepare(`SELECT id, CASE WHEN json_valid(provider_specific_data)
    THEN coalesce(json_extract(provider_specific_data,'$.baseUrl'),
    json_extract(provider_specific_data,'$.baseURL')) END AS url FROM provider_connections`).all()
    .filter(r => {try {const u = new URL(r.url); return u.protocol === 'https:' &&
      u.hostname === 'api.refuseless.com' && /^\/v1\/?$/.test(u.pathname) && !u.port; } catch {return false;}})
    .map(r => r.id);
  if (!ids.length) return {rows: [], providers: {}, identity: 'unavailable'};
  const rows = db.prepare(`SELECT date(timestamp) AS date, provider, model,
    'refuseless' AS bucket, usage_provenance, status, count(*) AS requests,
    sum(tokens_in) AS prompt, sum(tokens_out) AS output,
    sum(coalesce(tokens_cache_read,0)) AS cache_read,
    sum(coalesce(tokens_cache_creation,0)) AS cache_write,
    sum(CASE WHEN tokens_in IS NULL OR tokens_out IS NULL OR tokens_in < 0 OR tokens_out < 0
      OR coalesce(tokens_cache_read,0) < 0 OR coalesce(tokens_cache_creation,0) < 0
      OR tokens_in < coalesce(tokens_cache_read,0)+coalesce(tokens_cache_creation,0)
      THEN 1 ELSE 0 END) AS invalid
    FROM call_logs WHERE connection_id IN (${ids.map(() => '?').join(',')})
      AND julianday(timestamp) < julianday(?)
    GROUP BY date(timestamp), provider, model, usage_provenance, status`).all(...ids, end);
  return {rows, providers: Object.fromEntries(rows.map(r => [r.provider, false])), identity: 'connection_endpoint'};
});
process.stdout.write(JSON.stringify(snapshot()));
db.close();
