# OpenClaw: hourly committed aggregates

HTTP never collects OpenClaw usage, including cold/stale/corrupt/missing artifacts,
manual refresh, period changes and display/update. The default reader opens only
data/openclaw-committed.json. There is no SDK/CLI fallback or executor. Legacy
SQLite saved-window codecs remain for explicit offline imports/tests, not HTTP.

## Source and correctness

openclaw_committed.py reads SQLite with mode=ro and query_only, a 1 MiB page
cache, a 200 ms lock timeout, a monotonic 25-second deadline/progress handler,
at most 64 agent DBs, 20,000 rollup headers and 64 MiB aggregate bytes. It opens
only agents/*/agent/openclaw-agent.sqlite, never the shared state/openclaw.sqlite,
configuration/auth, transcripts, backup APIs or managed DB staging. Its authorizer
refuses reads outside cache_entries. Leading-scope indexed queries select rollup
headers, then exact-key quarter/partition pairs with matching updated_at.
Neither rollup nor partition bodies are selected.

This is an explicitly pinned private on-disk adapter, not a public API:
OpenClaw 2026.10.10 source 736f5f7bff2d28a2125058544e5c671b6edb1f3d,
rollup-v3 envelope version 11 / format 1 / projection 3,
quarters-v1 format 2, projection-v2 pairing. Relevant upstream sources:

- src/infra/session-cost-usage-rollup-codec.ts
- src/infra/session-cost-usage-partitions.ts
- src/infra/session-cost-usage-quarters.ts and quarters.worker.ts
- src/gateway/session-cost-usage-lifetime.ts

The existing Gateway lifetime updates committed projections on session usage
publications, startup and catalog changes, independently of open report pages.
This reader does not request refresh/backfill or execute the producer.

One 90-calendar-day UTC artifact contains independently measured per-model/day
input, output, cacheRead, cacheWrite, totalTokens and cost vectors. Smaller
windows filter those rows and sum each counter separately. Providers may report
totalTokens different from the sum of categories: preserve the measurements and
expose categoryMismatchModelDays, never rebalance. Per-model owned quarter vectors
override whole-quarter totals when multiple models share a quarter.

Coverage is ALWAYS partial: no live transcript freshness/inventory is queried.
The artifact reports collection time separately from oldest/newest producer
computation times, missing newer days, old-format exclusions, temporary archive
materializations and ambiguous identities. Versions 6/8 are explicitly excluded;
unknown newer versions, missing paired quarters, corruption, budget exhaustion
or lock/deadline errors preserve the last admitted artifact and enter backoff.
Legacy numeric aggregates are eligible without opening their named JSONL file;
temporary archive materialization keys are excluded. Multiple artifacts claiming
one instance are all excluded because disjointness is not provable without a
content read. Thus committedRowsComplete can also be false. These omissions
must not be presented as measured zero or a complete all-time/session total.

The UI says committed snapshot / live coverage unverified. Collection older
than two hours or an uncovered range is stale; freshness of collection does not
certify freshness of underlying activity. Atomic fsync+replace publication never
publishes partially written JSON. Readers validate size/schema/counters/range.

## Install and operate

Reuse the existing .venv; no dependencies need installation.

    .venv/bin/python scripts/install-openclaw-collector
    systemctl --user start ai-usage-openclaw-collect.service
    systemctl --user show ai-usage-openclaw-collect.service -p Result -p ControlGroup
    systemctl --user enable --now ai-usage-openclaw-collect.timer

The installer changes only these two user units. The timer is hourly with up to
60 seconds jitter, Persistent=false (no missed-run queue). There is no collector
daemon. flock and a pre-attempt persisted next_allowed_at enforce cross-process,
restart-safe spacing: success >=1 hour, errors 1/2/4-hour exponential backoff.
Manual runs use the same service, not a bypassing CLI. To override backoff after
a diagnosed fix, first verify inactive/empty cgroup, then explicitly set
next_allowed_at=0 in the owned state JSON. Never delete the last-good artifact.

The exact nondelegated service cgroup is mandatory. Startup refuses extra
members and persists blocked=true, so a previous unkillable task cannot trigger
new reads. Unit limits: 35-second startup deadline, 5-second stop escalation,
KillMode=control-group, SendSIGKILL, Restart=no, MemoryMax=256M, TasksMax=16,
CPUQuota=50%, IOWeight=10. Network and fork/clone syscalls are denied; no child
processes or threads are needed. Systemd owns cleanup on success/error/timeout/
cancel, including escaped setsid descendants, and PID1 reaps orphans. No process-
group-only promise is made. D-state kernel I/O can delay even SIGKILL: never
declare a nonempty cgroup clean; investigate before clearing blocked state.
The kernel cannot guarantee both instantaneous D-state removal and a hard
wall-clock cleanup deadline.

Override OPENCLAW_USAGE_AGENTS_DIR / OPENCLAW_AGGREGATE_SNAPSHOT only in the
collector unit, and the latter in Dashboard too if moved. Custom/nondefault
stores require a reviewed adapter/configuration, not automatic global DB lookup.

## Verification / rollback

    .venv/bin/python -m pytest tests/ -v
    RUN_SYSTEMD_FAULT_TESTS=1 .venv/bin/python -m pytest tests/test_openclaw_systemd.py -v

Offline tests cover actual wire fixtures, model-owned vectors, token categories,
1/7/30/90 days, 100 requests across missing/corrupt/stale snapshots, no subprocess
or upstream calls, lock/backoff, cancel, budget/version/pair faults and failed
atomic publication. Opt-in disposable units inject grandchildren, setsid and
TERM ignoring on leader success/error, timeout and cancellation; assert empty
cgroups and no surviving/zombie injected PIDs. They never modify production units.

Rollback: disable/stop the collector timer and service, retain the artifact,
then revert the feature commit and restart Dashboard only if restoring the old
request-driven collector is explicitly desired. The safer operational rollback
is to keep snapshot-only HTTP and the last-good data with its original timestamp.
No OpenClaw source/config/Gateway restart is needed for this feature.
