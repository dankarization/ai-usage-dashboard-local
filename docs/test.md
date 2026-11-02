# Test and service checks

## Python suite

Run from the repository root with the project virtual environment:

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
```

The offline suite covers aggregation, pricing, FastAPI response contracts, cache fallback, and dashboard page rendering. Tests marked `live_api` or `live_antigravity` require separately available provider credentials or a running local service and are not part of the routine offline check.

## Running service

Start `scripts/ai-usage-service` for a local instance, or inspect the existing `ai-usage-dashboard.service` user unit. Check these endpoints without printing private payload contents:

```bash
curl -fsS http://127.0.0.1:7995/health
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7995/dashboard
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:7995/api/v1/quotas
curl -fsS -o /dev/null -w '%{http_code}\n' 'http://127.0.0.1:7995/api/v1/model-breakdown?days=7&daily=false'
```

`/health` should return `status=ok`; the other checks should return HTTP 200. `GET /api/v1/quotas` reads only the current memory/disk snapshot and does not force provider refreshes. The dashboard refreshes through `POST /api/v1/display/update` (serialized with other updates). A forced live refresh contacts configured sources, so use it only when needed to verify collector behavior.

## Private artifacts

```bash
git check-ignore .env token_usage_eink.json token_usage_dashboard.png usage.json cursor.csv glm.json update.log antigravity_sync_metadata.json tmp/example.txt
```

Keep local credentials, exports, generated payloads/charts, logs, and unredacted screenshots outside the tracked tree. The committed dashboard screenshot has two opaque account-label masks and no metadata.

## Accounting and read-cache contracts

Usage-day windows are UTC for Gateway, Hermes, router observations and account
analytics; snapshot timestamps still use the viewer's clock. The legacy export payload
keeps its existing host-local date window independently of model analytics. The model chart
uses agent rows, while the separate account chart has only all-model daily totals.
Do not infer account-model days from small agent samples. Partial router retention
must not suppress older agent bucket days. Independent provider rows sharing an
account model name are not account-route comparisons.

`daily=false` omits presentation arrays after overlap reconciliation; it must not
change account/residual tokens. Malformed Gateway aggregates cannot replace a
valid saved window. Partial, refreshing, stale and unavailable sources cannot
complete the canonical overview. Daily overlap residuals are withheld when per-day caps contradict the model-window
reconciliation. Rejected credit data is admitted atomically, never partially shown
as a valid balance. Duplicate account dates are not summed as a
complete period; omitted top models remain explicitly unattributed.

The in-process read cache is bounded to 32 projections, keyed by exact UTC window,
source and daily flag. Healthy reads live for 30 seconds; degraded results back
off for 5 seconds. Same-key concurrent reads share collection.
`refresh=true` bypasses this layer (not upstream rate-limit caches). A completed
full refresh invalidates it and fences earlier in-flight publications. Original
fetch timestamps remain unchanged on hits. Quota arrivals do not rebuild the
model chart/table. Concurrent full refreshes coalesce; cold GET and POST exports
share one writer lock. First-time SQLite WAL/schema initialization has a separate
bounded cross-process guard, so simultaneous cold windows cannot race the journal
mode transition; ordinary snapshot transactions remain SQLite-owned.

Focused regressions: `tests/test_source_cache.py`, `tests/test_openclaw_usage.py`,
`tests/test_nordrouter_bucket_exclusion.py`, `tests/test_omniroute_usage.py`,
`tests/dashboard_dom_harness.js`. Browser acceptance must additionally verify both
charts, mobile containment, quota disclosure, period switches and refresh errors.
