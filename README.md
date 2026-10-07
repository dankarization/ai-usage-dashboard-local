# AI Usage Dashboard

AI Usage Dashboard is a local-first token usage dashboard for Codex, Claude Code, OpenCode, Cursor, and GLM/Z.ai. It aggregates local usage data, estimates API-equivalent cost from public pricing assumptions, and can produce a terminal table, a desktop chart, and an optional e-paper JSON payload.

It is not a cloud monitoring service and does not upload your usage data. Raw exports, generated charts, JSON payloads, and local logs stay on your machine and are excluded by `.gitignore`.

## Installation

```bash
git clone <this-repo> ai_usage_dashboard
cd ai_usage_dashboard
cp .env.example .env
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
```

`.env` is only for local private configuration. Start by enabling only the data sources you actually use.

## Data Source Setup

You do not need every platform connected on day one. The tool enables each source independently based on what exists on your machine:

- **Codex**: If you use Codex CLI, the tool reads local Codex sessions by default. No API key is required.
- **Claude Code**: If you use Claude Code, the tool reads local Claude Code project JSONL logs by default. No API key is required.
- **OpenCode**: If you use OpenCode, the tool reads the main local OpenCode SQLite database by default. If you also use `opencode_skill` for archive querying, set `AI_USAGE_OPENCODE_SKILL_PATH` in `.env`.
- **Cursor**: To include Cursor dashboard exports, set `CURSOR_COOKIE` in `.env`. This browser cookie must stay private. With the cookie present, the dashboard also fetches `GET /api/usage-summary` and adds the two monthly quota windows shown on the Cursor spending page to the unified `quotas` array: `Cursor Models` (the "Cursor models" bar = Composer + Cursor's own models, `autoPercentUsed`) and `Cursor Other` (the "Other models" bar = other named/frontier models, `apiPercentUsed`), both resetting at the billing-cycle end. An expired cookie returns a non-JSON login page, which is rejected so the last good cached snapshot (`cursor_usage_summary.json`) is preserved.
- **Grok**: To include SuperGrok / X Premium weekly usage pool, set `GROK_COOKIE` in `.env` (browser cookie from grok.com while logged in). This cookie must stay private. A 0% week omits the float field in the grpc-web response (proto3 default); the parser maps that to 0% so the quota bar still appears. The legacy local-source rollup's `grok` category can use OpenCode data; the new history uses only OpenClaw's xAI route.
  Grok Bot has a separate weekly limit, read through grok.com's authenticated bot relay (`bot.usage`) with the same cookie. Its percentage and reset are shown in a distinct row; if the relay cannot be read, that row remains **unavailable** rather than copying the SuperGrok weekly pool value.
- **GLM/Z.ai**: To include the GLM/Z.ai usage API, set `GLM_BEARER_TOKEN` in `.env`. This bearer token must stay private.

A minimal `.env` can be empty. The tool will still use local sources it can discover automatically; sources without credentials are skipped or read from existing local caches.

The current version uses each tool's default local data directory. If you need custom paths, prefer the source tool's own configuration or explicit variables such as `AI_USAGE_OPENCODE_SKILL_PATH`. Do not put personal absolute paths in public documentation.

## NordRouter and two Codex profiles

Set `NORDROUTER_API_KEY` in the private `.env`. The terminal table and desktop
chart include a separate NordRouter token category; the terminal summary and
`/api/v1/quotas` expose balance, today/7d/30d spend and top-five model rankings.
Actual reported NordRouter spend is added to the estimated total cost.
If the same requests are also logged in a local source, those sources overlap;
provider usage is additive, not cross-provider request-deduplicated.

Analytics/balance attempts are cached for 12 minutes, usage pages for 5 minutes,
including failed attempts. Cross-process locks and per-endpoint budgets limit
bursts. API errors preserve stale data with `status=stale`. Private caches live in
`data/nordrouter/`, scoped by a hash of the key; keys, IPs and client metadata are
not stored. Model-breakdown requests use the same analytics cache and budget.

Analytics totals do not necessarily equal their calendar daily buckets. Today
uses timestamped usage rows converted to **Asia/Tbilisi**, scanning newest-first
pages until local midnight (up to eight pages per refresh). If this bound is
exceeded or pages are unavailable, `today_complete=false` marks partial top-model
rankings; spend falls back to the server daily bucket matching the Tbilisi date.
`today_basis` distinguishes this fallback. Daily table/chart tokens and costs
retain the server's daily buckets; 7d/30d spend and 7d ranking use the server
analytics windows. Consequently window summary spend may differ from the daily
column sum. Analytics retention is at most 90 days. Model breakdown returns the
upstream model totals and spend, with empty daily arrays (the API does not expose
per-model daily buckets).

The primary Codex profile defaults to `~/.codex` (or `CODEX_HOME`). Set
`CODEX_HOME_2=~/.codex-secondary` to enable a second existing profile, optionally
with `CODEX_LABEL_1` and `CODEX_LABEL_2`. Authenticate that profile separately
with Codex before use; this dashboard does not perform login. Each profile uses
its own `auth.json` and optional ChatGPT account ID for quota requests, with its
own session-log fallback. Missing profiles still appear as placeholders with
unknown (null) percentages, never 0% used / 100% remaining.

To enable the second account, create a separate Codex home and sign in there,
then point the dashboard at it:

```bash
CODEX_HOME=~/.codex-secondary codex login
```

```bash
# private .env
CODEX_HOME_2=~/.codex-secondary
CODEX_LABEL_2=you@example.com
```

Codex writes that account's own `auth.json` under the second home, and the
dashboard reads it read-only to request that account's quota. A login held by a
separate tool is not reused: those stores are private to that tool, this
dashboard does not export credentials out of them, and a second Codex login is
the supported way to give it a credential it may read.

`npx ccusage codex daily` runs separately with each profile's `CODEX_HOME`;
local daily tokens and estimated costs are summed into the GPT category. An
identical resolved home is counted once. Per-profile exports are cached in
`data/codex/` and successful exports rebuild the aggregate `usage.json`. Node/npm
are required; the first run may install ccusage. The deprecated standalone
`@ccusage/codex` package is not used.

Generate the cache before using the cache-only quotas endpoint:

```bash
.venv/bin/python auto_usage.py -d 7 --skip-desktop-chart
.venv/bin/python -m uvicorn local_display_service:app --host 127.0.0.1 --port 7995
# In another terminal:
curl http://127.0.0.1:7995/api/v1/quotas
curl 'http://127.0.0.1:7995/api/v1/model-breakdown?days=7'
```

## Usage

```bash
# Last 7 days
.venv/bin/python auto_usage.py -d 7

# Last 30 days, text + E1002 JSON only, no desktop chart
.venv/bin/python auto_usage.py -d 30 --skip-desktop-chart

# Skip cost estimation
.venv/bin/python auto_usage.py -d 7 --no-cost
```

Outputs:

- Terminal table: daily token counts, AI Hours, and estimated cost.
- `token_usage_dashboard.png`: desktop matplotlib chart.
- `token_usage_eink.json`: structured JSON for the E1002 and local display service.

These files are local private artifacts and are ignored by default.

## Local Display Service

The FastAPI service can serve the latest dashboard JSON to local devices such as an e-paper display.

```bash
scripts/ai-usage-service
```

Default endpoints:

- `http://127.0.0.1:7995/health`
- `http://127.0.0.1:7995/token_usage.json`
- `http://127.0.0.1:7995/api/v1/quotas`
- `http://127.0.0.1:7995/api/v1/model-breakdown`
- `http://127.0.0.1:7995/api/v1/display/update`

`GET /api/v1/quotas` is the compact automation endpoint. It returns each
provider window's used and remaining percentages plus reset timestamps, without
forcing a provider refresh. The web page triggers a provider refresh on opening
and every five minutes while open. Multiple automatic clients share a recent
snapshot; the manual Refresh button always forces a new collection. Other API
clients can call `POST /api/v1/display/update` when fresh data is required.
For Grok, `product_usage` may contain product codes and their contributions to
the same shared weekly credit pool. These percentages are not token counts or
separate product limits. Only verified codes 2 (Grok Build) and 4 (Grok Chat)
are named; other codes stay unknown. Grok Bot remains a separate quota row.

`GET /api/v1/model-breakdown` returns per-model token usage (input, output,
cache_read, cache_write, total) from the OpenClaw Gateway, including its
NordRouter route for temporary side-by-side comparison. Canonical NordRouter
tokens and billed spend come directly from its analytics API; NordRouter-routed
Gateway rows are visible in OpenClaw model usage but excluded from Overview,
cost estimates and daily history to avoid double counting. The
Gateway's `byModel` rollup supplies token-type window totals, while its
`modelDaily` rows supply day × provider × model **total** tokens only; daily
input/output/cache fields are therefore `null`. NordRouter model totals cover
the window, and `source_daily.nordrouter` holds account-wide daily totals, not
invented per-model daily splits. `provider` is the reported route; model rows
omit account identity because the Gateway aggregate does not identify provider
accounts (including which Codex login). Source status is in `meta`; `sources`
gives direct NordRouter daily-bucket tokens and billed window cost alongside
non-NordRouter Gateway tokens and modeled cost. The OpenClaw cost sums only
priced models and explicitly labels the estimate partial when models lack
price data; a zero or missing price is not fabricated. The direct model-cost rows are
window aggregates and may not sum exactly to direct account daily buckets.
The web dashboard has one 30d (default) / 7d selector for Overview, history,
direct NordRouter model costs, OpenClaw model usage, and selected-window
NordRouter metrics. Balance, today's spend/models, and provider quota/reset
windows retain their own inherent periods. Query params:

- `days` (default 30): number of days to cover.
- `daily` (default true): set to `false` to omit per-day entries and return
  totals only.

Full refreshes are serialized inside the API process because collection updates
shared cache files. If multiple clients call `POST /api/v1/display/update` at the
same time, later requests wait for the active refresh before running. Cached GET
requests remain read-only and do not acquire the refresh lock.

If a LAN device needs access, configure the host through private local config or your own launch script. Do not commit fixed private IP addresses.

## Data Sources

- Codex: `npx @ccusage/codex@latest --json`
- Cursor: `cursor.com/api/dashboard/export-usage-events-csv` (usage) and `cursor.com/api/usage-summary` (monthly quota), with a private browser cookie
- GLM/Z.ai: usage API, with a private bearer token
- Claude Code: local Claude Code JSONL session logs
- DeepSeek Harness (DSH): local `~/.dsh/sessions` logs, plain or Zstandard-compressed JSONL (the `zstd` binary must be on PATH for compressed logs). Each session directory may hold several format generations (`session.jsonl` plus `session.vN.jsonl`, each optionally `.zstd`); every generation is a full replay of the session, so only the newest generation per session directory is counted. Z.ai GLM usage routed through DSH joins the GLM bucket because the Z.ai usage API does not see it; local models (e.g. LM Studio) are reported in the Other bucket at $0
- OpenCode: local OpenCode SQLite database; optional archive support can use a separate `opencode_skill` installation

All paths and credentials are local environment details. The public repository documents contracts only; it does not include real data.

## E-Paper Reference Implementation

`eink/` is optional hardware reference code, not part of the normal installation path. Most users only need the terminal table, desktop chart, and local JSON output.

The current reference implementation targets the **Seeed Studio reTerminal E1002** 800x480 color e-paper display. If you have that device, see `eink/README.md` and `eink/e1002/README.md`.

Hardware configuration lives in `eink/e1002/secrets.h` next to the Arduino sketch. That file contains Wi-Fi and local service URLs and must not be committed. The public template is `eink/e1002/secrets.h.example`:

```cpp
#define AI_USAGE_WIFI_SSID "YOUR_WIFI_SSID"
#define AI_USAGE_WIFI_PASSWORD "YOUR_WIFI_PASSWORD"

#define AI_USAGE_DASHBOARD_UPDATE_URL "http://YOUR_LOCAL_HOST:7995/api/v1/display/update"
#define AI_USAGE_DASHBOARD_CACHED_URL "http://YOUR_LOCAL_HOST:7995/token_usage.json"
#define AI_USAGE_DASHBOARD_DEVICE_ID "example-e1002"
```

Normal setup does not require `secrets.h`. Create it only when compiling or flashing the reTerminal E1002 sketch.

## For AI Agents

The repo-local root skill is:

```text
skills/skill_ai_usage_dashboard.md
```

When a user asks to inspect AI usage, estimate token cost, refresh the local dashboard, or debug the E1002 JSON contract, read this skill first. Workspace-level skill files can point to this file; the repo-local file is the source of truth.

## Development And Verification

```bash
.venv/bin/python -m pytest tests/ -v
.venv/bin/python -c "import tomllib; tomllib.load(open('pyproject.toml','rb'))"
git check-ignore .env token_usage_eink.json token_usage_dashboard.png usage.json cursor.csv glm.json update.log tmp/example.txt
```

Before publishing, also run a privacy scan for fixed private IPs, personal absolute paths, private deployment domains, old workspace paths, and secret-manager references.

See `docs/test.md` for the fuller test strategy.

## Local web dashboard

Start the existing service with
`python -m uvicorn local_display_service:app --host 127.0.0.1 --port 7995`
and open `http://127.0.0.1:7995/dashboard` (or `/`). The self-contained dark
page uses no CDN or additional dependencies. It displays NordRouter balance,
spend and top models, each Codex profile's quotas, a generic **Quotas** section
covering every other provider returned by `/api/v1/quotas` (for example GLM,
Cursor, Ollama, Claude, Antigravity and Grok), plus Gateway/direct-NordRouter
daily history and model usage for seven days. The overview above it remains a
separately labelled legacy local-source 30-day rollup. Each quota card shows its window label, used percentage,
a progress bar and the reset time. Unknown costs and quotas appear as `—`,
never as zero.

The page calls `POST /api/v1/display/update` every five minutes (and when
**Refresh** is pressed), then reloads the quota and model-breakdown endpoints.
Snapshot timestamps, provider stale status, and incomplete NordRouter today
data are shown; network failures preserve the last displayed values. Dates use
`DD.MM.YYYY`; timestamp instants use the browser's local timezone and 24-hour
`HH:mm` format. Legacy timestamps without timezone remain labelled server time.


## Privacy

This repository is designed to be publishable with only fake examples. Real cookies, bearer tokens, Wi-Fi credentials, local usage exports, generated charts, generated JSON payloads, and logs must remain in private ignored files.
