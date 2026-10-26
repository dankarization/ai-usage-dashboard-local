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
import grok_usage
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
    description="Without source, returns the cached automation view without refreshing. source=codex, grok, or grok_bot fetches only that provider live, independently of the full dashboard refresh.",
)
def quotas(source: Literal['all', 'codex', 'grok', 'grok_bot'] = 'all') -> dict[str, Any]:
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
        if source == 'codex':
            snapshots = load_all_codex_quotas()
        else:
            cookie = os.environ.get('GROK_COOKIE', '')
            if source == 'grok':
                snapshots = grok_usage.export_grok_quota(cookie) if cookie else []
            else:
                snapshots = grok_usage.export_grok_bot_quota(cookie) if cookie else [{
                    'provider': 'grok_bot', 'label': 'Weekly Grok Bot Limit', 'status': 'unavailable',
                }]
        payload = {"meta": {"generated_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds")},
                   "quotas": snapshots}
    quota_items = []
    for item in payload.get("quotas") or []:
        used_percentage = max(0, min(100, int(item["percentage"]))) if item.get("percentage") is not None else None
        quota_items.append({
            **{k: item[k] for k in ("account", "account_label", "status", "balance_usd", "spend_today_usd", "spend_7d_usd", "spend_30d_usd", "top_models_today", "top_models_7d", "today_complete", "today_basis", "product_usage") if k in item},
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
    description="Returns OpenClaw Gateway usage, Hermes native usage, canonical NordRouter analytics and optional non-additive router-wide bucket observations. source=openclaw|hermes|nordrouter|omniroute reads one independently. Bucket prices sum retained serving attempts at exact current tariffs; bucket_usage includes route/day drilldown and coverage. Agent bucket tokens are preserved; router-wide USD is not an agent allocation and never enters overview/account totals. Direct NordRouter model totals are account-window aggregates including mixed direct/router usage. Pass ?daily=false to omit daily entries.",
)
def model_breakdown(days: int = 30, daily: bool = True, source: Literal['all', 'openclaw', 'hermes', 'nordrouter', 'omniroute'] = 'all') -> dict[str, Any]:
    days = max(1, min(days, 90))
    # Reads Gateway aggregates and NordRouter's independently locked cache;
    # do not queue a period switch behind an unrelated full provider refresh.
    load_env()
    if source == 'all':
        return build_model_breakdown(days=days, include_daily=daily)
    return build_model_breakdown(days=days, include_daily=daily, source=source)


@app.post(
    "/api/v1/display/update",
    response_model=DashboardPayload,
    summary="Force a dashboard refresh and return the fresh payload",
    description="Triggers a full recompute of the dashboard payload (exporting from local logs and the Z.ai API when configured) and returns the fresh payload. Falls back to the cached payload or the on-disk token_usage_eink.json if the refresh fails.",
)
def display_update(request: UpdateRequest, response: Response) -> dict[str, Any]:
    global _cached_payload, _last_successful_refresh
    # Collection writes shared cache files, so refreshes must not overlap.
    with _refresh_lock:
        if (request.reason == "auto_refresh" and _cached_payload is not None
                and time.monotonic() - _last_successful_refresh < AUTO_REFRESH_MIN_SECONDS):
            response.headers["X-Dashboard-Refresh"] = "cached"
            return _cached_payload
        try:
            _cached_payload = generate_latest_payload()
            _last_successful_refresh = time.monotonic()
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
