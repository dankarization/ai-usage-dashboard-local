# OpenCodex на Macbook — opt-in, non-additive retained source

## Version evidence and scope

Owner-side evidence identifies the installed app as **2.99.0** and confirms
`ocx usage --range 7d --json` returns model/provider daily aggregates. Raw output
also contains accounts: **that JSON must never leave the Mac**. This candidate
has not contacted the Mac, inspected its installation or used any credentials.

Public release [`v2.99.0`](https://github.com/lidge-jun/opencodex/tree/667662903a6c139524506667d12656d9ad50fad0)
is pinned to **`667662903a6c139524506667d12656d9ad50fad0`**; its `package.json`
says 2.99.0. This replaces the earlier prototype's dev pin. Primary evidence:

- [`src/cli/observe.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/cli/observe.ts),
  usage command: accepts `--range all --surface all --since <ms> --until <ms>
  --json`; forwards to the runtime usage API and verifies echoed custom bounds.
  Without `--search`, JSON is the complete returned object, not human top-10 rows.
- [`src/cli/runtime-api.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/cli/runtime-api.ts),
  `printData`: direct JSON serialization, **no `{data:...}` wrapper**.
- [`src/usage/time-range.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/usage/time-range.ts):
  both bounds required; **inclusive integer epoch milliseconds**. One UTC date
  is `[00:00:00.000Z, next 00:00:00.000Z minus 1 ms]`.
- [`src/usage/summary.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/usage/summary.ts):
  all-history `days[]` uses local dates and caps at **366**; do NOT backfill from
  it. `snapshotWindowStart/End` records full retained-ledger scalar boundaries
  **before custom-window filtering**. For each exact UTC window use `models[]`,
  not `days[]`. Model breakdown caps at 256 and collapses overflow to
  `other/other`; this adapter refuses that day, rather than inventing identity.
- [`src/server/management/logs-usage-routes.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/server/management/logs-usage-routes.ts):
  custom bounds select independent filtered aggregates, return exact-window echo,
  full-ledger boundary metadata and explicit truncation/oversize diagnostics.
  Read errors return HTTP 500, not genuine zero measurements.
- [`src/usage/cost.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/usage/cost.ts):
  `measuredRequests` includes **reported AND estimated** usage.
- [`src/cli/usage-report.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/cli/usage-report.ts):
  upstream cost is an **API list-price equivalent estimate, not a receipt**;
  unmatched prices/unmetered requests are excluded. Upstream configured price
  overlays may affect this estimate; no dashboard price inference is performed.

Matching a version string/public tag does not attest the installed binary or
runtime. Owner must verify that the running CLI AND runtime honor this contract.
Connected-client/hub-scoped results, searched/filtered views and pin drift are
refused. No account/provider/model join is available or invented. Timeline is
unused: it collapses tails above 24 series and cannot establish exact identity.

## Shared project configuration and local HTTP mode

OpenCodex settings live in the same ignored project **`.env`** as the other
sources. `.env.example` contains only disabled, generic settings:

```dotenv
OPENCODEX_USAGE_ENABLED=0
OPENCODEX_API_BASE_URL=http://127.0.0.1:12345
OPENCODEX_USAGE_SOURCE_ID=local-opencodex
OPENCODEX_USAGE_DB=
```

The port is a placeholder, not an assertion about an installation. The API base
is an **address only**, never a credential. Environment variables override file
values. `project_config.load_env` is shared with the existing `auto_usage.load_env`
entry point, preserving its simple KEY=value syntax (no shell/quote expansion).
Dashboard source reads and the independent collector use this same configuration.
Real addresses, keys and private files must never be committed.

After explicit local configuration/enablement, `python -m opencodex_collect
--days 2` reads `OPENCODEX_API_BASE_URL` without requiring the address in arguments.
`--base-url` is an explicit per-run override; `--input FILE` selects file fallback
instead and never contacts the configured API. HTTP collection still only happens
in the independent oneshot, never on dashboard page/API reads. No scheduler is
installed and no new inbound port/listener is opened.

The only transport operation is **GET `/api/usage`** with `range=all`,
`surface=all` and exact inclusive `since`/`until` epoch-ms bounds for each UTC date.
Allowed base: `http://127.0.0.1:PORT` or `http://[::1]:PORT`, explicit port, root
path. Hostnames, non-loopback/LAN/remote destinations, HTTPS, URL credentials,
queries/fragments, redirects, proxy environment, cookies, compression, and auth
headers are refused/unused. This same-host mode is NOT permission to tunnel or
forward the Mac's management listener. It does not attest which process owns the
loopback port: the operator must verify the selected local installation.

Upstream response JSON may contain account aggregates. Only the bounded response
in local RAM is read; `project_window` immediately discards accounts and all
non-allowlisted metadata. Only minimized schema-2 observations reach SQLite.
Nothing raw is logged or persisted. Across machines, use the Mac-local producer
and transfer only minimized files, never the raw API JSON.

**Important verified upstream constraint:** pinned 2.99.0
[`serve-options.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/server/index/serve-options.ts)
checks `requireManagementAuth` before dispatching `/api/*`;
[`management-auth.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/server/management-auth.ts)
returns 401 without an admitted principal (503 when admission is unavailable).
Loopback by itself is not authorization. The existing CLI
[`runtime-api.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/cli/runtime-api.ts)
uses `runningProxyUpdateHeaders`; pinned
[`login-cli.ts`](https://github.com/lidge-jun/opencodex/blob/667662903a6c139524506667d12656d9ad50fad0/src/oauth/login-cli.ts)
adds its locally configured management credential. This adapter deliberately
**does not discover, copy, send, refresh or mint** that credential/capability.

Consequently direct HTTP against ordinary pinned 2.99.0 is expected to fail
closed with 401: **configuration alone does not make protected HTTP collection
work**. 401/403/503/non-200 return a safe error and preserve all saved observations,
with no retry, side-endpoint probe or automatic CLI/auth fallback. Do not weaken
upstream protection or expose management to make it pass. The usable fallback is
an explicitly owner-run **Mac-local CLI producer**, using the CLI's own existing
local admission, followed by allowlisted file import. Direct HTTP success is
verified with synthetic loopback fixtures only, not an installed OpenCodex runtime.

## Mac-local, read-only producer (proposal, not installed here)

`opencodex_export.py` plus `opencodex_usage.py` use Python 3.11+ stdlib only.
After separate owner approval, they can run **on the Mac** alongside the verified
CLI. They execute only this read command, with no shell, login, config or startup
mutation:

```text
ocx usage --range all --surface all --json --since START_MS --until END_MS
```

Raw CLI stdout stays in bounded RAM **on the Mac**; stderr is discarded. Neither
raw stdout, accounts, apps, API-key IDs, filters, logs, prompts, request IDs,
credentials nor arbitrary metadata are written or forwarded. Only the closed
allowlist below is atomically exported to files in an operator-selected 0700
directory (files 0600). Existing completed files survive timeout, oversized,
invalid or truncated output. There is no listener, admin forwarding, new auth,
transport, schedule, startup job or automatic deployment in this patch.

### Exact minimized file contract: schema 2

Full, executable **synthetic example**:
[`tests/fixtures/opencodex-aggregate-v2.json`](../tests/fixtures/opencodex-aggregate-v2.json).
It is not real usage and is never a live fallback. The older raw-shaped fixture
is synthetic upstream-contract evidence only, NOT the transfer format.

Top-level keys, **exactly**:

```text
schema: 2
upstream_sha: "667662903a6c139524506667d12656d9ad50fad0"
source_id: opaque stable installation/ledger ID, [A-Za-z0-9_-]{1,64}
start_date, end_date: canonical YYYY-MM-DD, inclusive UTC dates
windows: [{date: YYYY-MM-DD, response: <allowlisted exact-day response>}]
```

Exactly one window for every declared date; no duplicates/gaps; **1–30 days per
file, at most 8 MiB**. Schema 2 refuses unexpected keys at every envelope/window/
summary/model level, including `accounts`. The producer pin is a compatibility
assertion, not cryptographic provenance. File transfer must independently bind
the producer, source ID and verified installation. Never reuse one source ID for
a different Mac/recreated ledger, or import the same ledger under multiple IDs.

`response` has exactly the following structural/diagnostic fields plus its two
aggregate objects:

```text
range="all", surface="all", customWindow=true,
since, until, generatedAt: integer epoch ms,
historyTruncated=false, entriesTruncated=false,
entriesDropped=0, truncatedPrefixBytes=0,
summary: {...}, models: [{...}]
```

Required `summary`: `requests`, `reportedRequests`, `estimatedRequests`,
`measuredRequests`, `unreportedRequests`, `unsupportedRequests`, `totalTokens`,
`coverageRatio`. Required each model: `provider`, `model`, `requests`,
`measuredRequests`, `reportedRequests`, `estimatedRequests`, `totalTokens`,
`inputTokens`, `outputTokens`.

Additional allowlisted numeric fields (preserve if present, otherwise omit):

- Both: `attemptCount`, `pricedRequests`, `unpricedRequests`, `estimatedCostUsd`,
  `inputTokens`, `outputTokens`, `cachedInputTokens`, `cacheReadInputTokens`,
  `cacheCreationInputTokens`, `reasoningOutputTokens`, `cacheObservedInputTokens`.
- Summary only: `unmeteredRequests`, `sends`, `settledSends`, `unresolvedSends`,
  `spendRequests`, `cacheObservedRequests`, `cacheSynthesizedRequests`,
  `cacheUnknownRequests`.
- Models only: `priceCoverageRatio`; identity metadata `resolvedModel`
  (string), `hasUnresolvedRequestedModel` (boolean).

`SUMMARY_FIELDS`, `MODEL_FIELDS`, `WINDOW_FIELDS` in `opencodex_usage.py` are the
executable roster. Counts are nonnegative safe integers, cost finite/nonnegative,
ratios 0–1. Exact labels are preserved (including prefixes/aliases), not renamed,
collapsed or treated as proven billing/serving identity. Accounts always unknown;
**no account metadata** is retained even on legacy raw-fixture import.

### Coverage, counters, price and failure semantics

A valid complete response containing **98% measured requests is accepted**.
That is not a truncated response. The upstream status partition must reconcile:

```text
measuredRequests = reportedRequests + estimatedRequests
requests = measuredRequests + unreportedRequests + unsupportedRequests
coverageRatio = requests ? measuredRequests / requests : 0
```

Raw reported token/counter values are retained separately in `reported_counters`;
upstream estimated USD is also retained as `reported_estimated_cost_usd`.
No scaling to 100%, no filling unknown requests with average tokens/prices.
Model request counts may overlap for combos: do not sum them into request totals.
Raw model token totals must reconcile to the exact-window summary token total.
Cache reads are not added again to total tokens or upstream inclusive input.
Missing token categories stay null; period categories become null if any
contributing model-day omits them. If a source zero-initializes an entirely
unmetered positive-request row, raw zero is kept but display usage is **unknown**,
not measured zero. A successful no-request window really is zero.

Cost is source-reported, separately labelled `source_reported_api_list_price_not_spend`.
Zero estimated USD with no priced requests is not a free bill: display unknown,
keep raw zero and price counters. Unpriced/unmetered usage remains excluded, not
invented. Balance and billed spend remain null. Unresolved sends are counters,
not grounds to discard valid reported partial telemetry.

Refuse transport errors, timeout, invalid JSON/duplicate fields/nonfinite values,
wrong/missing bounds, missing windows, malformed counters, incompatible source pin,
filters, `usageIncomplete`, truncation flags, model overflow/collapse and
unreconciled token totals. Validate the whole batch before SQLite. A rejected
batch cannot change any prior rows or collection timestamps. Valid partial
measurement can replace an earlier observation: it is a new authoritative
window, not a claim that missing tokens are zero. Deleted/cleaned source history
cannot be recovered or certified complete from these flags.

### Backfill and hourly collection: independently bounded

Full retained-history discovery runs **locally on the Mac** once using
`ocx usage --range all --surface all --json`; only its scalar
`snapshotWindowStart/End` determines the first UTC date. Raw discovery JSON stays
in RAM. Capped `days[]` is ignored. Every date from that first retained date
through today is exported in exact windows, in bounded batches. No 90-day prune,
no age expiry on the dashboard. SQLite key is `(source_id, UTC date)`.

Owner-run proposal, after confirming prerequisites (not executed here):

```sh
python3 -m opencodex_export --output-dir PRIVATE_EXPORT_DIR --source-id private-mac \
  --batch-days 7 --max-batches 8
```

Default maximum is one batch; `--max-batches` is an explicit invocation budget.
Each batch has a whole 60-second deadline by default (maximum 300), 2 MiB per CLI
stdout. Receipt has `complete` and `next_start_date`. **Backfill is complete only
when `complete=true`**. If budget is exhausted, resume with that exact
`--start-date`; do not assume the first file/all-history chart covers everything.
Each prior completed batch survives a later batch failure. Larger/slow histories
may need smaller batches, larger explicit deadline or resumed invocations; never
increase scope to raw request/log export. A day exceeding the 256-identity limit
is an explicit blocker, not silently collapsed history.

An independent hourly job, only if separately authorized, can export yesterday
and today with explicit UTC `--start-date`/`--end-date`, `--batch-days 2
--max-batches 1`. Transfer only schema-2 files over an owner-approved private file
channel. No dashboard credentials/remote admin listener are needed. Late changes
older than the overlap require an explicit overlapping backfill; full ledger
coverage cannot be inferred from a two-day incremental. No scheduler is installed.

On dashboard host, separately enable the source and choose a private, independent
**new v2 cache** (never production/shared agent DB):

```sh
OPENCODEX_USAGE_ENABLED=1 OPENCODEX_USAGE_SOURCE_ID=private-mac \
OPENCODEX_USAGE_DB=PRIVATE_CACHE_PATH \
python -m opencodex_collect --input APPROVED_AGGREGATE_FILE
```

Import completed files oldest-first. Repeating the identical file changes zero
rows. Newer observations replace day rows, **not add deltas**; older/conflicting
same-generation batches refuse atomically. Renamed/repeated files therefore do
not double count. The same ID/date is the identity, not filename or collection
time. Version-1 prototype caches are refused untouched, not migrated (they may
have account metadata); use a new v2 cache and reimport minimized files.
Legacy raw-shaped schema-1 input remains only for offline compatibility tests;
never use it as a Mac transfer format.

Local HTTP mode uses the same normalized projection as the file/CLI path, but
accepts the native aggregate response and drops non-allowlisted metadata in RAM.
Its admission limitations are described above. It never forwards management
credentials or exposes an admin endpoint; use CLI/file fallback on protected
upstream installations.

## Dashboard isolation and offline behavior

Disabled unless `OPENCODEX_USAGE_ENABLED=1`; disabled HTML is byte-identical to the
existing page. The history source selector has **OpenClaw**, **Hermes** and the
exact **OpenCodex на Macbook** label, alongside unchanged canonical overview.
OpenClaw/Hermes source-only observations read already-loaded period data; selecting
them does not collect, alter original payloads or infer an account split.
OpenCodex uses its own 1/7/30/**all saved history** selector. Its API is
`GET /api/v1/opencodex?days=0|1|7|30` (`0` means all retained observations).

OpenCodex never enters canonical overview/history totals, shared agent ingestion,
pricing tables or billing; there is no verified cross-source dedup yet. A source
switch never relabels another source's cached graph. Loading/API failures retain
last successful **same-source** graph/data, with unknown for a source never loaded.
Source dates remain visible and cache windows stay anchored to the latest saved
day while the Mac is offline. **No stale badge** or age pruning. Missing dates
are unknown, not zero. Page/API reads never contact the Mac or collect.

## Operational cost estimate (not measured live)

- **No LLM/API token spend is introduced**: these are read-only local aggregate
  queries/file imports, not inference. Displayed estimated USD is historical
  upstream telemetry, not the operational cost of this collector.
- Backfill: one discovery plus one query per UTC date. For 41 dates, **42 CLI
  queries** (not per-request network exports); output contains only bounded
  model summaries. Active-day count in the app is NOT the calendar backfill span;
  only retained-ledger boundaries determine that interval. Upstream exact-window aggregation may scan its retained ledger
  for each cold window: worst-case work is O(retained rows × dates). Runtime caches
  may reduce this. Actual Mac time/CPU/RAM must be measured by the owner.
- Hourly two-day overlap: two queries/invocation, **48/day**; no recurring full
  backfill. Default deadline at most 60 seconds of batch elapsed time per run;
  not an assertion of actual CPU or guaranteed completion. Only small aggregate
  files cross the approved private channel, never raw logs/accounts.
- SQLite storage grows with retained days × model identities, not request count;
  no dependencies, paid service, always-on listener or extra environment.
  All-history rendering/read cost grows with retained history; 1/7/30 are bounded
  normal views. Monitor storage rather than silently pruning old observations.

## Acceptance, verification, integration and rollback

Offline synthetic tests cover 121 retained days (>90), actual duplicate imports,
partial 98% telemetry, missing-vs-zero, raw counter/estimate preservation, account
allowlisting, failed batches, exact provider/model grouping, source isolation and
canonical input immutability. Real-script DOM tests and an offline full-page
Chromium fixture exercise source switching and canonical restoration. These are
not proof of the installed Mac export or requesting viewer delivery.

```sh
python -m pytest tests/test_opencodex_{usage,transport,panel,export}.py -q \
  --basetemp=proofs/pytest-tmp -o cache_dir=proofs/pytest-cache
```

Before live acceptance, owner must verify: installed CLI/runtime 2.99.0 contract,
all-history scalar bounds, custom UTC echo, no client/filter/search scoping,
allowlist-only output with no accounts, actual backfill `complete=true`, repeat
import dedup, original overview unchanged, same-source graph preserved after Mac
timeout/offline, and actual viewer rendering. A 2.99.0 version string plus a 7d
CLI sample does not yet prove those checks. Private file channel is also pending.

Integration is a focused patch: new modules/tests/docs, five setuptools entries,
three additive service hooks and a shared project dotenv loader;
`dashboard_page.py` and existing adapter collection logic remain untouched. Apply against current dirty canonical inputs by context, never replace
production files. Default rollback: unset the enable flag (exact original page),
remove only additive hooks if desired; retain the isolated cache for owner review.
No deployment, production DB/config, schedule, credentials, Mac/LAN interaction,
commit, push or PR occurred in this task.
