# AI Usage Dashboard

A self-hosted dashboard for AI token usage, provider quotas, and costs, based on
[grapeot/ai_usage_dashboard](https://github.com/grapeot/ai_usage_dashboard).

![Dashboard with account email addresses redacted](assets/dashboard-20261027-342844e234ae.png)

## What it shows

- Token usage by model and day, with 1-, 7-, and 30-day views.
- OpenClaw and Hermes usage, plus configured provider quota windows.
- NordRouter balance, billed spend, and top models.
- API-equivalent cost estimates, separate from billed spend.

Missing data stays unknown, not zero. If OpenClaw is unavailable, a saved window
for the same period is labelled with its original fetch time.

## Run locally

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/dankarization/ai-usage-dashboard-local.git
cd ai-usage-dashboard-local
cp .env.example .env
uv venv .venv
uv pip install --python .venv/bin/python -e '.[dev]'
scripts/ai-usage-service
```

Open **http://127.0.0.1:7995/**. Run commands from the repository root.
The default listener is loopback-only; set `AI_USAGE_HOST` and `AI_USAGE_PORT`
in the launch environment if needed. Use authenticated access when exposing it
beyond your machine: usage data and account details are private.

### Configure your sources

Edit the ignored `.env`, using [.env.example](.env.example) as the reference.
Enable only the sources you use:

- **OpenClaw:** a running local Gateway and a working OpenClaw CLI.
- **Hermes:** existing native profile databases; `HERMES_HOME` can override discovery.
- **Provider accounts:** optional keys/cookies and Codex profile settings listed in `.env.example`.
- **OmniRoute:** optional `OMNIROUTE_USAGE_CONTAINER` plus `NORDROUTER_API_KEY`.
  Reuses an existing container with read-only aggregate queries; the dashboard
  user must already have Docker access. It does not start or modify the router.

Never commit `.env`, credentials, local exports, caches, logs, or unredacted
screenshots. The page uses no CDN and does not upload usage to a dashboard cloud.

## How costs are estimated

**Estimated cost** means what the available agent token categories would cost
at the model's API rates, not the price of an OAuth/subscription plan. Input,
output, cache reads, and cache writes are priced separately. Without request-level
tiers, the estimate uses a **standard short-context baseline**; a large sum of
short requests does not trigger a long-context price. Unknown tariffs or token
categories remain visibly partial, never free.

**NordRouter billed cost** comes from its account API. Agent rows routed through
NordRouter are comparison-only, so they are not billed twice. OmniRoute bucket
costs are separate router-wide estimates, not allocations to individual agents
and not additions to the overview total.

## CLI, API, and checks

```bash
scripts/ai-usage -d 7
scripts/ai-usage -d 30 --skip-desktop-chart
MPLBACKEND=Agg .venv/bin/python -m pytest tests/ -v
```

Node/npm are needed only for Codex `ccusage` exports. The optional desktop chart
and cached dashboard payload are gitignored.

- `/health` — service status.
- `/api/v1/model-breakdown?days=7` — models, estimates, and pricing coverage.
- `/api/v1/quotas` — cached provider quotas.
- `/docs` and `/openapi.json` — API endpoints and field descriptions.
- [Test and service checks](docs/test.md) — focused verification and private artifacts.
