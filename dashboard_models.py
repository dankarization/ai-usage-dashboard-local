"""Pydantic response models for the AI Usage Dashboard local API.

These models are the source of truth for the FastAPI ``/openapi.json`` schema.
Every field carries a ``description`` so an AI agent reading the OpenAPI spec
can understand the payload without inspecting the source.

The models mirror the dict shape produced by ``auto_usage.build_eink_dashboard_payload``.
All fields are optional with defaults so a minimal cached payload (for example a
stale ``token_usage_eink.json`` written by an older version) still validates and
serializes without dropping fields. The service serializes with
``response_model_exclude_defaults=True`` so a minimal payload round-trips back to
its minimal shape.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class DashboardMeta(BaseModel):
    """Metadata describing the dashboard generation run."""

    version: int = Field(default=1, description='Payload schema version. Incremented on breaking shape changes.')
    generated_at: Optional[str] = Field(default=None, description='ISO timestamp (local, seconds precision) marking when the payload was generated. Legacy naive Pacific wall clock; prefer generated_at_utc for display.')
    generated_at_utc: Optional[str] = Field(default=None, description='Offset-aware UTC ISO timestamp (seconds precision) marking when the payload was generated. Consumers render it in their own timezone.')
    start_date: Optional[str] = Field(default=None, description='Inclusive start date of the daily window, YYYY-MM-DD.')
    end_date: Optional[str] = Field(default=None, description='Inclusive end date of the daily window, YYYY-MM-DD.')
    days: Optional[int] = Field(default=None, description='Number of days covered by the daily array, inclusive of both endpoints.')
    currency: Optional[str] = Field(default=None, description='Currency code for the cost fields, e.g. USD.')


class CategoryTotals(BaseModel):
    """Per-provider token totals summed across the whole date window."""

    nordrouter: int = Field(default=0, description='NordRouter account tokens from server daily analytics buckets.')
    cursor: int = Field(default=0, description='Total Cursor tokens for the window.')
    glm: int = Field(default=0, description='Total GLM / Z.ai tokens for the window (Z.ai usage API plus OpenCode-routed GLM).')
    gemini: int = Field(default=0, description='Total Google Gemini tokens for the window.')
    claude: int = Field(default=0, description='Total Anthropic Claude tokens for the window (Claude Code plus OpenCode-routed Anthropic).')
    gpt_opencode: int = Field(default=0, description='Total OpenAI GPT tokens for the window, including Codex.')
    deepseek: int = Field(default=0, description='Total DeepSeek tokens for the window.')
    grok: int = Field(default=0, description='Total xAI Grok tokens for the window (OpenCode xai/grok-* and related routes).')
    qwen: int = Field(default=0, description='Total Qwen tokens for the window (OpenCode/Antigravity routes whose provider or model name contains "qwen").')
    other: int = Field(default=0, description='Total tokens from providers not classified into the buckets above.')


class DashboardSummary(BaseModel):
    """Aggregate totals across the full date window."""

    total_tokens: int = Field(default=0, description='Sum of all provider token totals for the window.')
    total_ai_hours: float = Field(default=0.0, description='Cumulative estimated AI active time in hours across the window. Parallel agents are counted cumulatively, so this is total labor, not wall-clock coverage.')
    categories: CategoryTotals = Field(default_factory=CategoryTotals, description='Per-provider token totals for the window.')
    total_cost_usd: Optional[float] = Field(default=None, description='Estimated API-equivalent USD cost for the window. Present only when cost estimation is enabled.')


class DailyEntry(BaseModel):
    """One row of per-day token, active-time, and cost data."""

    date: Optional[str] = Field(default=None, description='Calendar date for this row, YYYY-MM-DD.')
    categories: CategoryTotals = Field(default_factory=CategoryTotals, description='Per-provider token totals for this day.')
    total_tokens: int = Field(default=0, description='Sum of all provider tokens for this day.')
    ai_hours: float = Field(default=0.0, description='Estimated AI active time for this day in hours.')
    cost_usd: Optional[float] = Field(default=None, description='Estimated API-equivalent USD cost for this day. Present only when cost estimation is enabled.')


class GlmQuotaUsageDetail(BaseModel):
    """Per-model usage breakdown for the monthly tool quota."""

    modelCode: Optional[str] = Field(default=None, description='Z.ai model/tool code, e.g. search-prime, web-reader, zread.')
    usage: Optional[int] = Field(default=None, description='Count consumed by this model/tool in the current window.')


class GlmQuotaSnapshot(BaseModel):
    """One rolling quota window for the Z.ai coding plan."""

    label: str = Field(description='Human-readable window label, e.g. "5 Hours Quota", "Weekly Quota", "Monthly Web Search / Reader / Zread Quota". Falls back to "<type> unit=<unit>" for unknown window codes.')
    type: str = Field(description='Raw Z.ai limit type, e.g. TOKENS_LIMIT or TIME_LIMIT.')
    unit: int = Field(description='Raw Z.ai unit code identifying the window within its type.')
    percentage: int = Field(default=0, description='Percentage of the window already used (0-100).')
    next_reset_time_ms: Optional[int] = Field(default=None, description='Epoch-millisecond timestamp at which the window resets. May be absent.')
    next_reset_iso: Optional[str] = Field(default=None, description='Local ISO timestamp (seconds precision) at which the window resets. Derived from next_reset_time_ms.')
    usage: Optional[int] = Field(default=None, description='Absolute count already used. Present only for the monthly tool quota (TIME_LIMIT).')
    current_value: Optional[int] = Field(default=None, description='Current consumed count. Present only for the monthly tool quota (TIME_LIMIT).')
    remaining: Optional[int] = Field(default=None, description='Remaining count before the window resets. Present only for the monthly tool quota (TIME_LIMIT).')
    usage_details: Optional[list[GlmQuotaUsageDetail]] = Field(default=None, description='Per-model usage breakdown. Present only for the monthly tool quota (TIME_LIMIT).')


class AccountMetrics(BaseModel):
    billing: Optional[dict] = Field(default=None, description="OpenAI API billing evidence; null funding/cycle fields mean unknown, never zero. Aggregate credits are not a subsidy allocation.")
    account: Optional[str] = Field(default=None, description='Stable Codex profile identity: codex_1 or codex_2.')
    account_label: Optional[str] = Field(default=None, description='Display name of the local Codex profile, when available.')
    plan_type: Optional[Literal['prolite', 'pro', 'pro500', 'plus']] = Field(default=None, description='Explicit Codex plan_type from the existing ChatGPT wham/usage response; unknown or historical fallback plans are omitted. Generic pro does not imply pro500.')
    status: Optional[str] = Field(default=None, description='ok, stale, unavailable, or not_configured.')
    balance_usd: Optional[float] = Field(default=None, description='NordRouter prepaid balance in USD.')
    spend_today_usd: Optional[float] = Field(default=None, description='Today spend; see today_basis and today_complete.')
    spend_7d_usd: Optional[float] = Field(default=None, description='NordRouter server 7-day analytics spend.')
    spend_30d_usd: Optional[float] = Field(default=None, description='NordRouter server 30-day analytics spend.')
    top_models_today: Optional[list[dict]] = Field(default=None, description='Top five local-day models by spend; partial if today_complete=false.')
    top_models_7d: Optional[list[dict]] = Field(default=None, description='Top five models by 7-day spend.')
    today_complete: Optional[bool] = Field(default=None, description='Whether all timestamped usage rows for the local day were collected.')
    today_basis: Optional[str] = Field(default=None, description='Time basis of today spend; fallback uses the server daily bucket.')


class QuotaSnapshot(AccountMetrics):
    """One rolling quota window for any provider (unified across GLM, Codex, Claude Code)."""

    provider: str = Field(description='Provider name, e.g. glm, codex, claude.')
    label: str = Field(description='Human-readable window label, e.g. "5 Hours", "Weekly", "5 Hours Quota".')
    percentage: Optional[int] = Field(default=None, description='Percentage of the window already used (0-100).')
    next_reset_time_ms: Optional[int] = Field(default=None, description='Epoch-millisecond timestamp at which the window resets. May be absent.')
    next_reset_iso: Optional[str] = Field(default=None, description='Local ISO timestamp (seconds precision) at which the window resets. Derived from next_reset_time_ms.')
    usage: Optional[int] = Field(default=None, description='Absolute count already used. Present only for the GLM monthly tool quota.')
    remaining: Optional[int] = Field(default=None, description='Remaining count before the window resets. Present only for the GLM monthly tool quota.')
    product_usage: Optional[list[dict]] = Field(default=None, description='Grok provider-defined product enum and usage_percent pairs; not token counts or separate limits.')


class AutomationQuotaSnapshot(AccountMetrics):
    """Automation-oriented view of one provider quota window."""

    provider: str = Field(description='Provider name, e.g. glm, codex, ollama, claude, or antigravity.')
    label: str = Field(description='Human-readable quota window label, e.g. "5h", "7d", or "Gemini 5h".')
    used_percentage: Optional[int] = Field(default=None, description='Percentage of the quota window already used (0-100).')
    remaining_percentage: Optional[int] = Field(default=None, description='Percentage of the quota window remaining (0-100), calculated as 100 minus used_percentage.')
    next_reset_time_ms: Optional[int] = Field(default=None, description='Epoch-millisecond timestamp at which the quota window resets. May be absent.')
    next_reset_iso: Optional[str] = Field(default=None, description='Local ISO timestamp at which the quota window resets. May be absent.')
    usage: Optional[int] = Field(default=None, description='Absolute count already used when the upstream provider exposes it.')
    remaining: Optional[int] = Field(default=None, description='Absolute count remaining when the upstream provider exposes it.')
    product_usage: Optional[list[dict]] = Field(default=None, description='Grok provider-defined product enum and usage_percent pairs; not token counts or separate limits.')


class QuotasResponse(BaseModel):
    """Compact quota response intended for scripts and agents."""

    generated_at: Optional[str] = Field(default=None, description='Generation time of a cached dashboard snapshot in local ISO format. Absent for live source responses. Naive Pacific wall clock kept for backward compatibility; prefer generated_at_utc.')
    generated_at_utc: Optional[str] = Field(default=None, description='Offset-aware UTC ISO timestamp (seconds precision) of the returned quota snapshot. Consumers render it in their own timezone.')
    quotas: list[AutomationQuotaSnapshot] = Field(default_factory=list, description='Current quota windows across all available providers.')


class DashboardPayload(BaseModel):
    """Full dashboard payload served by the local API and written to token_usage_eink.json."""

    meta: DashboardMeta = Field(default_factory=DashboardMeta, description='Metadata describing the generation run.')
    summary: DashboardSummary = Field(default_factory=DashboardSummary, description='Aggregate totals across the date window.')
    daily: list[DailyEntry] = Field(default_factory=list, description='One entry per day in the date window, ordered by date.')
    glm_quota: Optional[list[GlmQuotaSnapshot]] = Field(default=None, description='Z.ai coding-plan quota snapshots (5-hour, weekly, monthly windows). Present only when GLM_BEARER_TOKEN is set and the quota fetch succeeds. Deprecated alias of quotas; prefer quotas for new consumers.')
    quotas: Optional[list[QuotaSnapshot]] = Field(default=None, description='Unified quota snapshots across providers (GLM 5h/weekly/monthly, Codex 5h/weekly). Present when any provider quota is available. The web dashboard and stdout render this single array.')


class HealthResponse(BaseModel):
    """Service liveness probe."""

    status: str = Field(description='Service status, e.g. "ok".')
    service: str = Field(description='Service name, e.g. "ai_usage_dashboard".')
    generated_at: Optional[str] = Field(default=None, description='ISO timestamp (local, seconds precision) of the health check. Naive Pacific wall clock kept for backward compatibility; prefer generated_at_utc.')
    generated_at_utc: Optional[str] = Field(default=None, description='Offset-aware UTC ISO timestamp (seconds precision) of the health check.')


class UpdateRequest(BaseModel):
    """Request body for forcing a dashboard refresh."""

    reason: str = Field(description='Why the refresh is requested, e.g. force_button.')
    view: str = Field(description='Requested view window, e.g. "1d", "7d", or "30d".')
    device_id: str = Field(description='Identifier of the device requesting the refresh.')


class ModelTokenTotals(BaseModel):
    """Token totals for one model across the date window."""

    input: Optional[int] = Field(default=None, description='Non-cached input tokens. None when the source does not provide per-category breakdown.')
    output: Optional[int] = Field(default=None, description='Output tokens (including reasoning/thinking tokens). None when the source does not provide per-category breakdown.')
    cache_read: Optional[int] = Field(default=None, description='Cache-read input tokens. None when the source does not provide per-category breakdown.')
    cache_write: Optional[int] = Field(default=None, description='Cache-write input tokens. None when the source does not provide per-category breakdown.')
    total: int = Field(default=0, description='Total tokens for this model (input + output + cache_read + cache_write). Always present even when per-category breakdown is not.')


class ApiEstimateFields(BaseModel):
    """Coverage of a standard API-equivalent category estimate, never an invoice."""

    priced_tokens: Optional[int] = Field(default=None, description='Tokens with a known exact model/category tariff.')
    unpriced_tokens: Optional[int] = Field(default=None, description='Tokens without a known category or exact model tariff; never treated as free.')
    unpriced_categories: Optional[list[str]] = Field(default=None, description='Missing token categories, tariffs, or inconsistent/unclassified counts.')
    api_pricing: Optional[dict] = Field(default=None, description='Exact pricing identity, rates, USD/million_tokens unit, reference and standard short-context assumption.')


class ModelDailyEntry(ApiEstimateFields):
    """One day of token usage for one model."""

    date: str = Field(description='Calendar date, YYYY-MM-DD.')
    account_day_compatible: Optional[bool] = Field(default=None, description='False for a multi-day Hermes session aggregate: its last-activity day cannot establish account-day overlap.')
    input: Optional[int] = Field(default=None, description='Non-cached input tokens for this day. None when the source does not provide per-category breakdown.')
    output: Optional[int] = Field(default=None, description='Output tokens for this day. None when the source does not provide per-category breakdown.')
    cache_read: Optional[int] = Field(default=None, description='Cache-read input tokens for this day. None when the source does not provide per-category breakdown.')
    cache_write: Optional[int] = Field(default=None, description='Cache-write input tokens for this day. None when the source does not provide per-category breakdown.')
    total: int = Field(default=0, description='Total tokens for this day.')
    cost_usd: Optional[float] = Field(default=None, description='Agent rows use the same API policy as window totals; null when daily category counts are unavailable. Router observations retain their separate serving-route pricing semantics.')
    cost_kind: Optional[str] = Field(default=None, description='Agent API estimate coverage: estimated, partial or unknown. Router serving costs retain their existing scope.')
    known_cost_usd: Optional[float] = Field(default=None, description='Known retained cost, a lower bound when routes are unpriced.')
    unpriced_requests: Optional[int] = Field(default=None, description='Retained serving attempts without a known price.')


class ModelBreakdownEntry(ApiEstimateFields):
    """Per-model token usage from one data source."""

    cost_usd: Optional[float] = Field(default=None, description="Agent rows: API-equivalent known-category estimate, independent of native stored costs; may be partial. Direct NordRouter remains billed spend. OmniRoute observations sum retained serving routes at current tariffs; null for incomplete pricing, never an agent allocation.")

    cost_kind: Optional[str] = Field(default=None, description="Provenance of cost_usd: billed (provider invoice), estimated (standard API rates), partial (known category subtotal), or unknown. Comparison-only NordRouter rows retain their source provenance but are excluded from agent estimates.")
    bucket_usage: Optional[dict] = Field(default=None, description='Non-additive router-wide bucket observation: exact historical serving routes with tokens, estimated USD and daily drilldown; pricing freshness and retention coverage. Never a per-agent allocation or historical invoice.')
    bucket_comparison: bool = Field(default=False, description='Agent bucket tokens retained for comparison; USD is unknown without a matching router observation.')
    overview_cost_usd: Optional[float] = Field(default=None, description='Agent API estimate retained for overview only; never substituted for the non-additive router-wide bucket price.')
    overview_cost_kind: Optional[str] = Field(default=None, description='API estimate coverage kind retained for a non-additive agent bucket comparison.')
    overview_model_key: Optional[str] = Field(default=None, description='Agent model identity retained separately from the exact router bucket display key.')

    source: str = Field(description='Data source: openclaw, hermes, direct nordrouter, or non-additive omniroute bucket observations. Agent NordRouter-route rows are comparison-only and excluded from totals.')
    provider: Optional[str] = Field(default=None, description='Provider/route identifier reported by the source; not necessarily an account identity.')
    model: str = Field(description='Model identifier reported by the source; names alone do not prove account identity.')
    model_key: Optional[str] = Field(default=None, description='Display grouping key. Regular model spellings collapse routing/context aliases. OmniRoute bucket keys use omniroute/<exact bucket name> and never merge with serving/account model IDs; serving tariffs retain exact IDs separately.')
    exclusive_tokens: Optional[int] = Field(default=None, description='Direct NordRouter tokens outside proven NordRouter-origin bucket routes, plus any unpartitioned residual. Null when overlap cannot be proven. Never negative.')
    exclusive_status: Optional[str] = Field(default=None, description='partitioned, partial_retention, no_route_map, unavailable, or duplicate. partial_retention still subtracts only observed matches and may include older bucket traffic in the residual.')
    overlap_proven_tokens: Optional[int] = Field(default=None, description='NordRouter-origin serving tokens matched to this exact account model and removed from exclusive_tokens, capped at the account total.')
    overlap_unreconciled_tokens: Optional[int] = Field(default=None, description='Matched serving tokens above the account total. Kept on the bucket; not deducted twice and not shown as a negative residual.')
    overlap_unknown_tokens: Optional[int] = Field(default=None, description='Same-bucket NordRouter serving tokens with no exact account-model match. Not deducted.')
    account_billed_tokens: Optional[int] = Field(default=None, description='Unchanged NordRouter account-model token total. Not a residual and not added to bucket observations.')
    account_billed_cost_usd: Optional[float] = Field(default=None, description='Unchanged NordRouter account-model billed USD. Not split into a residual invoice and not added to router-wide estimates.')
    totals: ModelTokenTotals = Field(default_factory=ModelTokenTotals, description='Token totals for this model across the full date window. Direct NordRouter totals remain the account billed count; exclusive_tokens is the non-overlapping residual.')
    daily: list[ModelDailyEntry] = Field(default_factory=list, description='Per-day token entries for this model, ordered by date. Empty when ?daily=false is passed.')


class ModelBreakdownTotals(BaseModel):
    """Aggregate token totals across all models and sources."""

    input: int = Field(default=0, description='Total non-cached input tokens across all models that provide per-category breakdown.')
    output: int = Field(default=0, description='Total output tokens across all models that provide per-category breakdown.')
    cache_read: int = Field(default=0, description='Total cache-read tokens across all models that provide per-category breakdown.')
    cache_write: int = Field(default=0, description='Total cache-write tokens across all models that provide per-category breakdown.')
    total: Optional[int] = Field(default=None, description='Non-overlapping agent plus direct account tokens; null for unavailable/partial sources or unresolved agent-router/account overlap.')
    input_output_ratio: Optional[float] = Field(default=None, description='Input divided by output (non-cached input / output). None when output is zero.')
    cache_hit_rate: Optional[float] = Field(default=None, description='Cache-read tokens divided by (input + cache_read). None when the denominator is zero.')


class ModelBreakdownMeta(BaseModel):
    """Metadata for the model-breakdown response."""

    generated_at: Optional[str] = Field(default=None, description='ISO timestamp (local, seconds precision) marking when the breakdown was generated. Legacy naive Pacific wall clock; prefer generated_at_utc for display.')
    generated_at_utc: Optional[str] = Field(default=None, description='Offset-aware UTC ISO timestamp (seconds precision) marking when the breakdown was generated.')
    start_date: Optional[str] = Field(default=None, description='Inclusive start date, YYYY-MM-DD.')
    end_date: Optional[str] = Field(default=None, description='Inclusive end date, YYYY-MM-DD.')
    days: Optional[int] = Field(default=None, description='Number of days covered, inclusive of both endpoints.')
    day_timezone: str = Field(default='UTC', description='Calendar-day timezone shared by usage sources, distinct from the viewer-local snapshot clock.')
    openclaw_status: Optional[str] = Field(default=None, description='Displayed Gateway usage status: ok/partial/refreshing/stale/unavailable, cached when an exact saved window is retained during a refresh or degraded read, pending when not requested.')
    openclaw_cached_at_utc: Optional[str] = Field(default=None, description='Offset-aware UTC ISO timestamp of the original successful Gateway fetch that produced the saved snapshot currently displayed. Set when saved data is displayed; the value never advances on a failed or incomplete read.')
    openclaw_error: Optional[str] = Field(default=None, description='Bounded, non-secret failure summary for the latest attempted read. Saved data keeps its own observation timestamp.')
    openclaw_source_at_utc: Optional[str] = Field(default=None, description='Original observation instant of the displayed OpenClaw snapshot; never replaced by a failed or incomplete refresh time.')
    openclaw_upstream_status: Optional[str] = Field(default=None, description='Latest attempted read status, distinct from the status of saved data displayed.')
    openclaw_snapshot_complete: bool = Field(default=False, description='Whether displayed OpenClaw data came from a verified complete exact-window read. A saved complete snapshot is still not live.')
    openclaw_refreshing: bool = Field(default=False, description='A bounded request-driven read is in flight. Consumers may retry; saved data remains displayed.')
    openclaw_coverage: Optional[dict] = Field(default=None, description='Aggregate-only upstream completeness and observation interval, with no session content or credentials.')
    hermes_status: Optional[str] = Field(default=None, description='Hermes native session usage status: ok, not_configured when no Hermes state database exists, or unavailable when none could be read; pending when this source was not requested.')
    nordrouter_fetched_at_utc: Optional[str] = Field(default=None, description='Original provider fetch timestamp, not the time its cached response was read.')
    nordrouter_status: Optional[str] = Field(default=None, description='Direct NordRouter analytics status; pending when this source was not requested.')
    omniroute_status: Optional[str] = Field(default=None, description='Read-only retained router snapshot status: ok/stale/unavailable/not_configured/pending. Does not affect overview availability.')


class ModelBreakdownResponse(BaseModel):
    """Per-model token usage breakdown across all data sources."""

    meta: ModelBreakdownMeta = Field(default_factory=ModelBreakdownMeta, description='Metadata describing the generation run.')
    totals: ModelBreakdownTotals = Field(default_factory=ModelBreakdownTotals, description='Aggregate token totals across all models.')
    models: list[ModelBreakdownEntry] = Field(default_factory=list, description='Per-model entries, ordered by total tokens descending.')
    source_daily: Optional[dict] = Field(default=None, description='Source-level daily rows: NordRouter account buckets and non-additive OmniRoute observations, never to be summed together.')
    sources: Optional[dict] = Field(default=None, description='Existing overview sources (openclaw, hermes, nordrouter), per-agent comparison counts, and optional omniroute observations marked additive=false. Router coverage/cost never changes overview totals.')


class AntigravityIngestRequest(BaseModel):
    """Request body for pushing Antigravity entries from a satellite machine.

    Each entry follows the cache-entry shape used by
    ``antigravity_usage_cache.json``: model, timestamp (epoch-ms), token
    counts, and response_id for deduplication.
    """

    entries: list[dict] = Field(description='Antigravity usage entries from the satellite machine. Each entry should have: model, timestamp (epoch-ms int or ISO string), input, output, cache_read, cache_write, thinking, response_id, session_id.')
    source: Optional[str] = Field(default=None, description='Optional label identifying the satellite machine, e.g. "macbook-air". Used for logging only.')


class AntigravityIngestResponse(BaseModel):
    """Response body for the Antigravity ingest endpoint."""

    received: int = Field(description='Number of entries received in the request body.')
    new: int = Field(description='Number of entries that were new (not already in cache).')
    duplicate: int = Field(description='Number of entries that were already in cache (deduplicated by response_id).')
    total_cache: int = Field(description='Total number of entries in the cache after this ingest.')
