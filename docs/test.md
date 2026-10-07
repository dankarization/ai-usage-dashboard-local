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
