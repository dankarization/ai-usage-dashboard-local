// Runs INSIDE the existing router container. No auth, bodies, accounts, or API keys
// are selected. SQLite is read-only; one consistent transaction per snapshot.
'use strict';
const fs = require('fs');
const DB = require('better-sqlite3');
const { start, end } = JSON.parse(fs.readFileSync(0, 'utf8'));
if (![start, end].every(x => /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$/.test(x))) throw Error('Invalid bounds');
const db = new DB('/app/data/storage.sqlite', { readonly: true, fileMustExist: true });
db.pragma('query_only = ON');
const snapshot = db.transaction(() => {
  // Current connection metadata establishes account routing, NOT combo history.
  // Only a boolean origin classification leaves the process, never the config.
  const origins = new Map();
  for (const row of db.prepare(`SELECT provider,
      CASE WHEN json_valid(provider_specific_data) THEN coalesce(
        json_extract(provider_specific_data,'$.baseUrl'), json_extract(provider_specific_data,'$.baseURL')) END AS base_url
      FROM provider_connections`).all()) {
    let nord = row.provider === 'nordrouter';
    try {
      const url = new URL(row.base_url);
      nord ||= url.protocol === 'https:' && url.hostname === 'nordrouter.com' && /^\/v1\/?$/.test(url.pathname);
    } catch {}
    if (!origins.has(row.provider)) origins.set(row.provider, new Set());
    origins.get(row.provider).add(nord);
  }
  const providers = Object.fromEntries(Array.from(origins, ([id, values]) => [id, values.size === 1 ? [...values][0] : null]));
  // Rows have unique primary IDs. Do NOT dedup by correlation: real fallback
  // attempts share that ID and can each consume (and bill) tokens.
  // Group by Asia/Tbilisi calendar day (UTC+04, no DST), not request time.
  const rows = db.prepare(`SELECT combo_name AS bucket, provider, model,
      combo_execution_key AS route_path, usage_provenance, status,
      date(timestamp, '+4 hours') AS date,
      count(*) AS requests, sum(tokens_in) AS prompt, sum(tokens_out) AS output,
      sum(coalesce(tokens_cache_read,0)) AS cache_read,
      sum(coalesce(tokens_cache_creation,0)) AS cache_write,
      sum(CASE WHEN tokens_in IS NULL OR tokens_out IS NULL OR tokens_in < 0 OR tokens_out < 0
        OR coalesce(tokens_cache_read,0) < 0 OR coalesce(tokens_cache_creation,0) < 0
        OR tokens_in < coalesce(tokens_cache_read,0) + coalesce(tokens_cache_creation,0)
        THEN 1 ELSE 0 END) AS invalid
    FROM call_logs WHERE combo_name IS NOT NULL AND combo_name != ''
      AND connection_id IS NOT NULL
      AND julianday(timestamp) >= julianday(?) AND julianday(timestamp) < julianday(?)
    GROUP BY combo_name, provider, model, combo_execution_key, usage_provenance, status,
      date(timestamp, '+4 hours')`).all(start, end);
  const history = db.prepare('SELECT min(timestamp) AS first, max(timestamp) AS last FROM call_logs').get();
  return { rows, providers, history };
});
process.stdout.write(JSON.stringify(snapshot()));
db.close();
