"""Read-only OpenAI billing, separate from Codex and local agent usage."""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CREDITS_URL = "https://api.openai.com/v1/dashboard/billing/credit_grants"


def _money(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value >= 0 else None


def parse_credit_grants(data: Any) -> dict[str, Any]:
    """Aggregate credits do not establish a subsidy/purchased funding split.

    Do not classify grants from their amount, expiry or a guessed name.
    """
    if not isinstance(data, dict):
        raise ValueError("invalid billing object")
    amounts = {name: _money(data.get(key)) for name, key in (
        ("aggregate_total_usd", "total_granted"),
        ("aggregate_used_usd", "total_used"),
        ("aggregate_remaining_usd", "total_available"),
    )}
    if any(value is None for value in amounts.values()):
        raise ValueError("incomplete aggregate credit amounts")
    total, used, remaining = amounts.values()
    if abs(total - used - remaining) > .02:
        raise ValueError("inconsistent aggregate credits")
    grants = data.get("grants")
    rows = grants.get("data") if isinstance(grants, dict) else None
    expiries = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid grant")
            expiry = row.get("expires_at")
            if expiry is not None:
                if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= 0:
                    raise ValueError("invalid expiry")
                expiries.append(datetime.fromtimestamp(expiry, timezone.utc).isoformat())
    return {**amounts, "aggregate_grant_expiries": sorted(set(expiries))}


def _inference_key() -> str:
    """Reuse transcription credential read-only without changing speech config."""
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", str(Path.home() / ".openclaw/openclaw.json")))
    try:
        config = json.loads(path.read_text())
        skills = config.get("skills", {})
        entry = skills.get("entries", skills).get("openai-whisper-api", {})
        key = entry.get("apiKey") or entry.get("env", {}).get("OPENAI_API_KEY")
        return key if isinstance(key, str) else ""
    except (OSError, ValueError, AttributeError, TypeError):
        return ""


def export_openai_api_quota(timeout: float = 10) -> list[dict[str, Any]]:
    billing: dict[str, Any] = {
        "subsidy_total_usd": None, "subsidy_used_usd": None,
        "subsidy_remaining_usd": None, "purchased_balance_usd": None,
        "paid_usage_usd": None, "subsidized_tokens": None,
        "non_subsidized_tokens": None, "cycle_start": None,
        "cycle_end": None, "expires_at": None,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "OpenAI Platform credit grants",
    }
    snapshot = {"provider": "openai_api", "label": "API subsidy", "status": "unavailable", "billing": billing}
    session_key = os.environ.get("OPENAI_BILLING_SESSION_KEY", "")
    key = session_key or _inference_key()
    if not key:
        billing["detail"] = "No OpenAI credential available. Platform billing sign-in is required; Codex login is a separate pool."
        return [snapshot]
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    if os.environ.get("OPENAI_BILLING_ORG_ID"):
        headers["OpenAI-Organization"] = os.environ["OPENAI_BILLING_ORG_ID"]
    try:
        with urlopen(Request(CREDITS_URL, headers=headers), timeout=timeout) as response:
            data = json.load(response)
        billing.update(parse_credit_grants(data))
        snapshot["status"] = "partial"
        billing["detail"] = ("Aggregate credits verified, but this source does not identify ChatGPT subsidy versus purchased credits. "
                             "Cycle, paid spend and token funding allocation remain unknown.")
    except HTTPError as exc:
        # Never echo the body: provider errors can include credential fragments.
        billing["http_status"] = exc.code
        billing["detail"] = (
            "Billing requires an authenticated OpenAI Platform session for the linked organization; "
            "the existing transcription/project API key cannot read credit grants (HTTP " + str(exc.code) + "). "
            "An organization usage/admin key alone does not establish the subsidy or purchased balance."
            if exc.code in (401, 403) else "OpenAI billing request failed (HTTP " + str(exc.code) + ")."
        )
    except (URLError, TimeoutError, OSError):
        billing["detail"] = "OpenAI billing source is unreachable; balances remain unknown."
    except (ValueError, TypeError, OverflowError):
        billing["detail"] = "OpenAI billing response is incomplete or unsupported; balances remain unknown."
    return [snapshot]
