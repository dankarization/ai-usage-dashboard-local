from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import os
import threading
import time
from typing import Any, Literal

from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse

from dashboard_page import DASHBOARD_HTML

from auto_usage import build_latest_dashboard_payload, build_model_breakdown, ingest_antigravity_entries, load_all_codex_quotas, load_env
import openai_api_usage
import grok_usage
import auto_usage
from source_cache import SourceCache
from dashboard_models import (
    AntigravityIngestRequest,
    AntigravityIngestResponse,
    DashboardPayload,
    HealthResponse,
    ModelBreakdownResponse,
    QuotasResponse,
    UpdateRequest,
)

app = FastAPI(
    title="ai_usage_dashboard",
    description="Local API for the AI Usage Dashboard. Aggregates token usage, AI active time, USD cost, and unified provider quota snapshots. Includes a compact cache-only quota endpoint for automation clients.",
    version="0.1.0",
)

_cached_payload: dict[str, Any] | None = None
_payload_path = Path(__file__).resolve().parent / "token_usage_eink.json"
_refresh_lock = threading.Lock()
_last_successful_refresh = 0.0
AUTO_REFRESH_MIN_SECONDS = 4 * 60
_source_cache = SourceCache()


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
@app.get("/dashboard", response_class=HTMLResponse, summary="Local HTML dashboard")
def dashboard() -> str:
    return DASHBOARD_HTML


def _read_payload_from_disk() -> dict[str, Any] | None:
    if not _payload_path.exists():
        return None
    with open(_payload_path) as f:
        data = json.load(f)
    if isinstance(data, dict) and {"meta", "summary", "daily"}.issubset(data.keys()):
        return data
    return None


def read_cached_payload() -> dict[str, Any]:
    global _cached_payload
    if _cached_payload is not None:
        return _cached_payload
    # GET and POST share ownership of exports and generated files.
    with _refresh_lock:
        if _cached_payload is None:
            try:
                _cached_payload = generate_latest_payload()
            except Exception:
                disk_payload = _read_payload_from_disk()
                if disk_payload is None:
                    raise
                _cached_payload = disk_payload
        return _cached_payload


def generate_latest_payload() -> dict[str, Any]:
    return build_latest_dashboard_payload(days=30, no_cost=False, skip_desktop_chart=True)


def _pacific_naive_to_utc_iso(value: Any) -> str | None:
    """Resolve a legacy naive Pacific ``generated_at`` to an offset-aware UTC ISO.

    Older cached payloads predate ``generated_at_utc``. Their ``generated_at``
    is Pacific wall clock with no offset, so it must be labelled Pacific before
    conversion; otherwise a browser would read it as its own local time.
    """
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return parsed.astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds")


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Service liveness probe",
)
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "ai_usage_dashboard",
        "generated_at": datetime.now(ZoneInfo("America/Los_Angeles")).replace(tzinfo=None).isoformat(timespec="seconds"),
        "generated_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"),
    }


@app.get(
    "/token_usage.json",
    response_model=DashboardPayload,
    summary="Return the cached dashboard payload",
    description="Returns the dashboard payload: metadata, per-provider token totals, daily token/active-time/cost rows, and the GLM/Z.ai coding-plan quota snapshots when available. The payload is cached after the first successful generation and refreshed by POST /api/v1/display/update.",
)
def token_usage_json() -> dict[str, Any]:
    return read_cached_payload()


@app.get(
    "/api/v1/quotas",
    response_model=QuotasResponse,
    response_model_exclude_unset=True,
    summary="Return current provider quota windows",
    description="Without source, returns the cached automation view without refreshing. source=codex, grok, grok_bot, or openai_api reads only that provider independently, with bounded 30-second single-flight caching and original snapshot times.",
)
def quotas(source: Literal['all', 'codex', 'grok', 'grok_bot', 'openai_api'] = 'all', response: Response = None) -> dict[str, Any]:
    global _cached_payload
    if source == 'all':
        payload = _cached_payload
        if payload is None:
            payload = _read_payload_from_disk()
            if payload is not None:
                _cached_payload = payload
        if payload is None:
            payload = {"meta": {}, "quotas": []}
    else:
        load_env()
        def collect():
            if source == 'openai_api':
                snapshots = openai_api_usage.export_openai_api_quota()
            elif source == 'codex':
                snapshots = load_all_codex_quotas()
            else:
                cookie = os.environ.get('GROK_COOKIE', '')
                if source == 'grok':
                    snapshots = grok_usage.export_grok_quota(cookie) if cookie else []
                else:
                    snapshots = grok_usage.export_grok_bot_quota(cookie) if cookie else [{
                        'provider': 'grok_bot', 'label': 'Weekly Grok Bot Limit', 'status': 'unavailable',
                    }]
            return {"meta": {"generated_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")},
                    "quotas": snapshots}
        payload, status = _source_cache.get(('quotas', source), collect)
        if response is not None:
            response.headers['X-Dashboard-Source-Cache'] = status
    quota_items = []
    for item in payload.get("quotas") or []:
        used_percentage = max(0, min(100, int(item["percentage"]))) if item.get("percentage") is not None else None
        quota_items.append({
            **{k: item[k] for k in ("account", "account_label", "status", "balance_usd", "spend_today_usd", "today_provider_total_usd", "today_models_total_usd", "today_daily_bucket_usd", "spend_7d_usd", "spend_30d_usd", "top_models_today", "top_models_7d", "today_complete", "today_basis", "product_usage", "billing") if k in item},
            **({'plan_type': item['plan_type']} if item.get('provider') == 'codex' and isinstance(item.get('plan_type'), str) and item['plan_type'] in {'prolite', 'pro', 'pro500', 'plus'} else {}),
            "provider": item.get("provider", "unknown"),
            "label": item.get("label", "unknown"),
            "used_percentage": used_percentage,
            "remaining_percentage": 100 - used_percentage if used_percentage is not None else None,
            "next_reset_time_ms": item.get("next_reset_time_ms"),
            "next_reset_iso": item.get("next_reset_iso"),
            "usage": item.get("usage"),
            "remaining": item.get("remaining"),
        })
    meta = payload.get("meta") or {}
    return {
        "generated_at": meta.get("generated_at"),
        "generated_at_utc": meta.get("generated_at_utc") or _pacific_naive_to_utc_iso(meta.get("generated_at")),
        "quotas": quota_items,
    }


@app.get(
    "/api/v1/model-breakdown",
    response_model=ModelBreakdownResponse,
    summary="Return per-model token usage breakdown",
    description="Returns scheduled OpenClaw committed snapshots (never triggers collection), Hermes native usage, canonical NordRouter analytics and optional non-additive router-wide bucket observations. Agent costs use exact-model API rates and a standard short-context baseline, independent of subscription/native cost. Coverage fields distinguish partial/unknown categories. source=openclaw|hermes|nordrouter|omniroute reads one independently. Bucket prices sum retained serving attempts at exact current tariffs; bucket_usage includes route/day drilldown and coverage. Agent bucket tokens are preserved; router-wide USD is not an agent allocation and never enters overview or account billed totals. Direct NordRouter totals remain account billed counts. exclusive_tokens is the residual outside proven NordRouter-origin bucket routes; unmatched, unmapped, or unretained overlap is explicit and not silently dropped. All usage days are UTC. Account daily totals are not model-day attribution. A combined total is null when agent buckets can overlap account billing. Reads coalesce for 30 seconds; refresh=true bypasses this layer, not upstream rate-limit caches. Pass daily=false to omit daily entries without changing reconciliation.",
)
def model_breakdown(response: Response, days: int = 30, daily: bool = True, refresh: bool = False, source: Literal['all', 'openclaw', 'hermes', 'nordrouter', 'omniroute'] = 'all') -> dict[str, Any]:
    days = max(1, min(days, 90))
    # Reads the owned OpenClaw snapshot and NordRouter's independent cache;
    # do not queue a period switch behind an unrelated full provider refresh.
    load_env()
    # Include the UTC day in the identity: no prior-day snapshot crosses midnight.
    start, end, _, _ = auto_usage.get_model_date_range(days)
    key = ('models', start, end, days, daily, source)
    def collect():
        if source == 'all':
            return build_model_breakdown(days=days, include_daily=daily, openclaw_background=True)
        if source == 'openclaw':
            return build_model_breakdown(days=days, include_daily=daily, source=source, openclaw_background=True)
        return build_model_breakdown(days=days, include_daily=daily, source=source)
    value, status = _source_cache.get(key, collect, force=refresh)
    response.headers['X-Dashboard-Source-Cache'] = status
    return value


@app.post(
    "/api/v1/display/update",
    response_model=DashboardPayload,
    summary="Force a dashboard refresh and return the fresh payload",
    description="Triggers a full recompute of the dashboard payload (exporting from local logs and the Z.ai API when configured) and returns the fresh payload. Falls back to the cached payload or the on-disk token_usage_eink.json if the refresh fails.",
)
def display_update(request: UpdateRequest, response: Response) -> dict[str, Any]:
    global _cached_payload, _last_successful_refresh
    # A client joining an in-flight refresh receives that result, not a second
    # full export. Sequential manual refreshes remain explicit new reads.
    requested_at = time.monotonic()
    with _refresh_lock:
        if request.reason == 'auto_refresh' and _cached_payload is None:
            _cached_payload = _read_payload_from_disk()
            meta = (_cached_payload or {}).get('meta', {})
            stamp = meta.get('generated_at_utc') or _pacific_naive_to_utc_iso(meta.get('generated_at'))
            if stamp:
                try:
                    age = datetime.now(ZoneInfo('UTC')).timestamp() - datetime.fromisoformat(stamp).timestamp()
                    if 0 <= age < AUTO_REFRESH_MIN_SECONDS:
                        _last_successful_refresh = requested_at - age
                except (TypeError, ValueError):
                    pass
        if _cached_payload is not None and _last_successful_refresh >= requested_at:
            response.headers["X-Dashboard-Refresh"] = "coalesced"
            return _cached_payload
        if (request.reason == "auto_refresh" and _cached_payload is not None
                and time.monotonic() - _last_successful_refresh < AUTO_REFRESH_MIN_SECONDS):
            response.headers["X-Dashboard-Refresh"] = "cached"
            return _cached_payload
        try:
            _cached_payload = generate_latest_payload()
            _last_successful_refresh = time.monotonic()
            _source_cache.invalidate()
            response.headers["X-Dashboard-Refresh"] = "fresh"
        except Exception:
            response.headers["X-Dashboard-Refresh"] = "stale"
            if _cached_payload is not None:
                return _cached_payload
            disk_payload = _read_payload_from_disk()
            if disk_payload is not None:
                _cached_payload = disk_payload
                return _cached_payload
            raise
        return _cached_payload


@app.post(
    "/api/v1/antigravity/ingest",
    response_model=AntigravityIngestResponse,
    summary="Push Antigravity entries from a satellite machine",
    description="Receives Antigravity usage entries from a satellite machine (e.g. a laptop over Tailscale), deduplicates by response_id against the local cache, and persists the merged set. Intended for cross-machine aggregation: the dashboard host runs this endpoint, and the satellite uses scripts/push-antigravity to POST its entries.",
)
def antigravity_ingest(request: AntigravityIngestRequest) -> dict[str, Any]:
    # Serialize with full refreshes, which also rewrite the retained entry cache.
    with _refresh_lock:
        result = ingest_antigravity_entries(request.entries)
    if request.source:
        print(f"Antigravity ingest from {request.source}: received={result['received']} new={result['new']} dup={result['duplicate']} total={result['total_cache']}")
    return result
