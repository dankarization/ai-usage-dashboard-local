from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
import threading
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from dashboard_page import DASHBOARD_HTML

from auto_usage import build_latest_dashboard_payload, build_model_breakdown, ingest_antigravity_entries, load_env
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
    description="Returns a compact automation-oriented view of cached provider quotas, including used and remaining percentages plus reset timestamps. This endpoint reuses the dashboard cache and does not force provider refreshes.",
)
def quotas() -> dict[str, Any]:
    global _cached_payload
    payload = _cached_payload
    if payload is None:
        payload = _read_payload_from_disk()
        if payload is not None:
            _cached_payload = payload
    if payload is None:
        payload = {"meta": {}, "quotas": []}
    quota_items = []
    for item in payload.get("quotas") or []:
        used_percentage = max(0, min(100, int(item["percentage"]))) if item.get("percentage") is not None else None
        quota_items.append({
            **{k: item[k] for k in ("account", "status", "balance_usd", "spend_today_usd", "spend_7d_usd", "spend_30d_usd", "top_models_today", "top_models_7d", "today_complete", "today_basis") if k in item},
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
    description="Returns per-model token usage (input, output, cache_read, cache_write, total) across all data sources (OpenCode, Claude Code, Antigravity, Cursor, GLM, Codex). Sources that only provide total tokens have per-category fields set to null. Pass ?daily=false to omit per-day entries and return totals only. Note: Cursor, GLM, and Codex data come from the most recent export (typically 30 days); requesting days>30 may return incomplete data for those sources.",
)
def model_breakdown(days: int = 30, daily: bool = True) -> dict[str, Any]:
    days = max(1, min(days, 90))
    with _refresh_lock:
        load_env()
        return build_model_breakdown(days=days, include_daily=daily)


@app.post(
    "/api/v1/display/update",
    response_model=DashboardPayload,
    summary="Force a dashboard refresh and return the fresh payload",
    description="Triggers a full recompute of the dashboard payload (exporting from local logs and the Z.ai API when configured) and returns the fresh payload. Falls back to the cached payload or the on-disk token_usage_eink.json if the refresh fails.",
)
def display_update(request: UpdateRequest) -> dict[str, Any]:
    global _cached_payload
    # Collection writes shared cache files, so refreshes must not overlap.
    with _refresh_lock:
        try:
            _cached_payload = generate_latest_payload()
        except Exception:
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
    result = ingest_antigravity_entries(request.entries)
    if request.source:
        print(f"Antigravity ingest from {request.source}: received={result['received']} new={result['new']} dup={result['duplicate']} total={result['total_cache']}")
    return result
