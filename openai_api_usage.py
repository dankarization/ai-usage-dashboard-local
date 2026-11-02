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
    public_grants = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("invalid grant")
            expiry = row.get("expires_at")
            if expiry is not None:
                if isinstance(expiry, bool) or not isinstance(expiry, (int, float)) or not math.isfinite(expiry) or expiry <= 0:
                    raise ValueError("invalid expiry")
                expiries.append(datetime.fromtimestamp(expiry, timezone.utc).isoformat())
            if "grant_amount" in row and "used_amount" in row:
                amount, spent = _money(row["grant_amount"]), _money(row["used_amount"])
                if amount is None or spent is None or spent > amount + .02:
                    raise ValueError("invalid grant amounts")
                effective = row.get("effective_at")
                if isinstance(effective, bool) or not isinstance(effective, (int, float)) or not math.isfinite(effective):
                    raise ValueError("invalid grant effective time")
                public_grants.append({"total_usd": amount, "used_usd": spent,
                    "remaining_usd": max(0., amount - spent),
                    "effective_at": datetime.fromtimestamp(effective, timezone.utc).isoformat(),
                    "expires_at": datetime.fromtimestamp(expiry, timezone.utc).isoformat() if expiry else None,
                    "scheduled": row.get("is_scheduled") is True,
                    "funding": "unclassified"})
    return {**amounts, "aggregate_grant_expiries": sorted(set(expiries)), "grants": public_grants}


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


def _billing_session() -> dict[str, str]:
    """Read an operator-authorized session from a private, non-Git file."""
    if os.environ.get("OPENAI_BILLING_SESSION_KEY"):
        return {"session_key": os.environ["OPENAI_BILLING_SESSION_KEY"],
                "organization_id": os.environ.get("OPENAI_BILLING_ORG_ID", "")}
    path = Path(os.environ.get("OPENAI_BILLING_SESSION_FILE") or str(
        Path.home() / ".config/ai-usage-dashboard/openai-billing.json"))
    try:
        info = path.lstat()
        if not path.is_file() or path.is_symlink() or info.st_mode & 0o077 or info.st_uid != os.getuid():
            return {}
        data = json.loads(path.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("session_key"), str):
            return {}
        return {k: v for k, v in data.items() if k in
                ("session_key", "organization_id", "organization_name", "linked_account_name",
                 "link_state", "link_checked_at", "benefit_period_amount_usd", "benefit_cadence",
                 "benefit_expires_at", "benefit_checked_at") and isinstance(v, str)}
    except (OSError, ValueError, TypeError):
        return {}


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
    session = _billing_session()
    key = session.get("session_key") or _inference_key()
    if not key:
        billing["detail"] = "No OpenAI credential available. Platform billing sign-in is required; Codex login is a separate pool."
        return [snapshot]
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    org = os.environ.get("OPENAI_BILLING_ORG_ID") or session.get("organization_id")
    if org:
        headers["OpenAI-Organization"] = org
    for field in ("organization_name", "linked_account_name", "link_state", "link_checked_at"):
        if session.get(field):
            billing[field] = session[field]
    if session.get("benefit_checked_at"):
        try:
            period_amount = float(session["benefit_period_amount_usd"])
            expiry = datetime.fromisoformat(session["benefit_expires_at"].replace("Z", "+00:00"))
            checked = datetime.fromisoformat(session["benefit_checked_at"].replace("Z", "+00:00"))
            if _money(period_amount) is not None and expiry.tzinfo and checked.tzinfo:
                billing["benefit_period_amount_usd"] = period_amount
                for field in ("benefit_cadence", "benefit_expires_at", "benefit_checked_at"):
                    billing[field] = session.get(field)
                billing["benefit_snapshot_expired"] = expiry <= datetime.now(timezone.utc)
        except (ValueError, KeyError, TypeError, OverflowError):
            pass
    try:
        with urlopen(Request(CREDITS_URL, headers=headers), timeout=timeout) as response:
            data = json.load(response)
        billing.update(parse_credit_grants(data))
        if "total_paid_available" in data:
            paid = _money(data["total_paid_available"])
            if paid is None or paid > billing["aggregate_remaining_usd"] + .02:
                raise ValueError("invalid purchased balance")
            billing["purchased_balance_usd"] = paid
            billing["unclassified_free_remaining_usd"] = max(0., billing["aggregate_remaining_usd"] - paid)
        snapshot["status"] = "partial"
        snapshot["label"] = "All credits (funding unclassified)"
        total = billing["aggregate_total_usd"]
        if total > 0:
            snapshot["percentage"] = round(100 * billing["aggregate_used_usd"] / total)
        billing["detail"] = ("Credit-grant balances verified. Grant rows do not identify ChatGPT subsidy versus other funding. "
                             "Purchased balance is reported separately when supplied. Cycle, paid spend and token funding allocation remain unknown.")
    except HTTPError as exc:
        # Never echo the body: provider errors can include credential fragments.
        billing["http_status"] = exc.code
        billing["detail"] = (
            "The saved OpenAI Platform billing session expired or was denied (HTTP " + str(exc.code) + "). Reconnect the authorized Platform session."
            if session and exc.code in (401, 403) else
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
