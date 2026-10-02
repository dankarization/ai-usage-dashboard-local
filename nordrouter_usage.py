"""NordRouter account metrics with cross-process, credential-scoped caching.

Only aggregate metrics and sanitized usage rows are persisted, never keys/IPs.
Analytics windows are server-defined; today is selected by Tbilisi calendar date.
"""
import hashlib
import json
import os
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import fcntl

import requests

BASE_URL = "https://nordrouter.com/v1/account"
CACHE_DIR = Path(__file__).resolve().parent / "data" / "nordrouter"
TZ = ZoneInfo("Asia/Tbilisi")


def _sanitize(endpoint, body):
    if endpoint == "balance":
        return {"balance_usd": float(body["balance_usd"]), "currency": body["currency"]}
    if endpoint == "analytics":
        daily = [{"date": datetime.strptime(row["date"], "%Y-%m-%d").date().isoformat(),
                  "tokens": int(row["tokens"]), "amount_usd": float(row["amount_usd"])}
                 for row in body["daily"]]
        models = [{"id": str(row["id"]), "tokens": int(row["tokens"]),
                   "amount_usd": float(row["amount_usd"])} for row in body["top_models"]]
        return {"window_days": int(body["window_days"]),
                "totals": {"amount_usd": float(body["totals"]["amount_usd"])},
                "daily": daily, "top_models": models}
    if endpoint == "usage":
        rows = []
        for row in body["data"]:
            stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            rows.append({"created_at": stamp.isoformat(), "model": str(row["model"]),
                         "total_tokens": int(row["total_tokens"]), "cost_usd": float(row["cost_usd"])})
        return {"data": rows, "has_more": bool(body["has_more"])}
    raise ValueError("Unknown account endpoint")


class Client:
    def __init__(self, key, cache_dir=None):
        self.key = key
        self.root = Path(cache_dir or CACHE_DIR) / hashlib.sha256(key.encode()).hexdigest()[:16]

    def get(self, endpoint, **params):
        """At most one attempt per TTL, including failures. Return stale on errors."""
        ttl = 300 if endpoint == "usage" else 720
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.root / (endpoint + "-" + "-".join(f"{k}_{v}" for k, v in sorted(params.items())) + ".json")
        with (self.root / "lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                cache = json.loads(path.read_text())
            except (OSError, ValueError):
                cache = {}
            now = time.time()
            if now >= cache.get("retry_at", 0):
                # Global family budget also bounds different window/page queries.
                budget_path = self.root / ("usage-budget.json" if endpoint == "usage" else "analytics-budget.json")
                try:
                    budget = json.loads(budget_path.read_text())
                except (OSError, ValueError):
                    budget = []
                budget = [t for t in budget if now - t < 61]
                maximum = 8 if endpoint == "usage" else 4
                if len(budget) >= maximum:
                    return cache.get("data"), True
                budget.append(now)
                budget_path.write_text(json.dumps(budget))
                cache["retry_at"] = now + ttl
                try:
                    response = requests.get(f"{BASE_URL}/{endpoint}", params=params,
                                            headers={"Authorization": f"Bearer {self.key}"}, timeout=20)
                    response.raise_for_status()
                    data = _sanitize(endpoint, response.json())
                    cache.update(data=data, fetched_at=now, failed=False)
                except (requests.RequestException, ValueError, KeyError, TypeError):
                    cache["failed"] = True
                temp = path.with_suffix(".tmp")
                temp.write_text(json.dumps(cache))
                temp.replace(path)
            return cache.get("data"), bool(cache.get("failed") or not cache.get("data"))


def collect(days=30, *, key=None, cache_dir=None, now=None):
    key = key if key is not None else os.environ.get("NORDROUTER_API_KEY", "")
    if not key:
        return {}
    client = Client(key, cache_dir)
    now = now or datetime.now(TZ)
    today = now.astimezone(TZ).date().isoformat()
    stale = False
    windows = {}
    # Fixed windows avoid issuing requests for every arbitrary dashboard range.
    for window in (30, 7):
        body, old = client.get("analytics", days=window)
        windows[window] = body or {}
        stale |= old
    if days > 30:
        body, old = client.get("analytics", days=90)
        windows[90] = body or {}
        stale |= old
    balance, old = client.get("balance")
    stale |= old
    daily = windows.get(90, windows[30]).get("daily", [])
    today_row = next((row for row in daily if row["date"] == today), None)
    # Usage provides timestamped rows for local-day model ranking. Bound to eight
    # pages per refresh (<10/min), explicitly flag partial results on large days.
    by_model = defaultdict(lambda: {"tokens": 0, "amount_usd": 0.0})
    complete = False
    for page in range(8):
        usage, old = client.get("usage", days=2, limit=100, page=page)
        stale |= old
        if not usage:
            break
        stamps = []
        for row in usage["data"]:
            stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
            stamps.append(stamp)
            if stamp.astimezone(TZ).date().isoformat() == today:
                model = by_model[row["model"]]
                model["tokens"] += int(row["total_tokens"])
                model["amount_usd"] += float(row["cost_usd"])
        # API pages are newest-first (verified against live responses). Once
        # an ordered page crosses local midnight, all of today's rows are seen.
        crossed_midnight = bool(stamps and stamps == sorted(stamps, reverse=True)
                                and stamps[-1].astimezone(TZ).date().isoformat() < today)
        if not usage["has_more"] or crossed_midnight:
            complete = True
            break
    top_today = sorted(({"id": model, **values} for model, values in by_model.items()),
                       key=lambda x: x["amount_usd"], reverse=True)[:5]
    return {
        "provider": "nordrouter", "label": "NordRouter USD",
        "status": "stale" if stale else "ok",
        "balance_usd": (balance or {}).get("balance_usd"),
        "spend_today_usd": round(sum(v["amount_usd"] for v in by_model.values()), 6) if complete else (today_row or {}).get("amount_usd"),
        "spend_7d_usd": windows[7].get("totals", {}).get("amount_usd"),
        "spend_30d_usd": windows[30].get("totals", {}).get("amount_usd"),
        "top_models_today": top_today,
        "top_models_7d": sorted(windows[7].get("top_models", []), key=lambda x: x["amount_usd"], reverse=True)[:5],
        "today_complete": complete,
        "today_basis": "Asia/Tbilisi timestamps" if complete else "server daily date (Asia/Tbilisi date selection)",
        "daily": daily,
    }


def quota(summary):
    return {k: v for k, v in summary.items() if k != "daily"}
