"""Mistral subscription allowances per account.

Reads data from:
1. Saved cookie files (persistent, no Chrome needed)
2. Chrome cookies via browser_cookie3 (real-time)
3. OmniRoute container API keys (fallback)
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import mistral_cookies

API = "https://api.mistral.ai"
CONTAINER = os.environ.get("OMNIROUTE_USAGE_CONTAINER", "omniroute")

# node script runs inside the omniroute container: reads + decrypts keys,
# calls the Mistral API, prints only non-secret JSON to stdout.
_NODE_SCRIPT = r"""
const fs = require("fs"), crypto = require("crypto"), https = require("https");
const env = fs.readFileSync("/app/data/server.env", "utf8");
const secret = (env.match(/STORAGE_ENCRYPTION_KEY=(.+)/) || [])[1].trim();
const key = crypto.scryptSync(secret, "omniroute-field-encryption-v1", 32);
const Database = require("better-sqlite3");
const db = new Database("/app/data/storage.sqlite", { readonly: true });
const rows = db.prepare(
  "SELECT name, api_key FROM provider_connections WHERE provider='mistral' AND auth_type='apikey' AND is_active=1 ORDER BY priority"
).all();
function dec(r) {
  const p = String(r.api_key).slice(7).split(":");
  const d = crypto.createDecipheriv("aes-256-gcm", key, Buffer.from(p[0], "hex"), { authTagLength: 16 });
  d.setAuthTag(Buffer.from(p[2], "hex"));
  return d.update(p[1], "hex", "utf8") + d.final("utf8");
}
function call(apiKey, path) {
  return new Promise((resolve) => {
    const req = https.request({ hostname: "api.mistral.ai", path, method: "GET",
      headers: { Authorization: "Bearer " + apiKey, Accept: "application/json" } }, res => {
      let b = ""; res.on("data", c => b += c); res.on("end", () => {
        try { resolve({ status: res.statusCode, data: JSON.parse(b) }); }
        catch { resolve({ status: res.statusCode, data: null }); }
      });
    });
    req.on("error", () => resolve({ status: 0, data: null }));
    req.setTimeout(8000, () => { req.destroy(); resolve({ status: 0, data: null }); });
    req.end();
  });
}
(async () => {
  const out = [];
  const seen = new Set();
  for (const r of rows) {
    if (!String(r.api_key).startsWith("enc:v1:")) continue;
    const k = dec(r);
    const me = await call(k, "/v1/users/me");
    const sub = await call(k, "/v1/billing/subscription");
    const email = me.data && me.data.email || null;
    if (email && seen.has(email)) continue; // one entry per account
    if (email) seen.add(email);
    out.push({
      account: r.name, email,
      workspace_id: me.data && me.data.workspace && me.data.workspace.id || null,
      organization_id: me.data && me.data.organization && me.data.organization.id || null,
      plan: sub.data && sub.data.plan_name || null,
      payg_enabled: !!(sub.data && sub.data.payg_enabled),
      vibe_payg_enabled: !!(sub.data && sub.data.vibe_payg_enabled),
      me_status: me.status, sub_status: sub.status,
    });
  }
  console.log(JSON.stringify(out));
})();
"""


def _container_exists() -> bool:
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=10)
        return CONTAINER in r.stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False


def _get_chrome_cookies() -> dict[str, str]:
    """Get Mistral cookies from the user's Chrome Default profile."""
    try:
        import browser_cookie3
        all_cookies = browser_cookie3.chrome()
        return {c.name: c.value for c in all_cookies if 'mistral' in (c.domain or '').lower()}
    except Exception:
        return {}


def _parse_subscription_html(html: str) -> dict[str, Any]:
    """Parse subscription data from the Mistral admin HTML page."""
    data: dict[str, Any] = {}
    
    # Extract plan
    plan_match = re.search(r'Current plan[^<]*<[^>]+>([^<]+)', html, re.IGNORECASE)
    if not plan_match:
        plan_match = re.search(r'>(Pro|Plus|Free|Enterprise)<', html)
    if plan_match:
        data['plan'] = plan_match.group(1).strip()
    
    # Extract status
    status_match = re.search(r'>(Active|Inactive)<', html)
    if status_match:
        data['status'] = status_match.group(1)
    
    # Extract free until date
    free_match = re.search(r'Free with your offer code until ([^<]+)', html)
    if free_match:
        data['free_until'] = free_match.group(1).strip()
    
    # Extract subscription price
    sub_match = re.search(r'Subscription[^$]*\$([\d.,]+)', html)
    if sub_match:
        data['subscription_price'] = float(sub_match.group(1).replace(',', ''))
    
    # Extract estimated total
    total_match = re.search(r'Estimated total[^$]*\$([\d.,]+)', html)
    if total_match:
        data['estimated_total'] = float(total_match.group(1).replace(',', ''))
    
    # Extract reset time
    reset_match = re.search(r'Resets in ([^<]+)', html)
    if reset_match:
        data['reset_in'] = reset_match.group(1).strip()
    
    # Extract API and Vibe usage from dollar amount pairs
    # Pattern: <span class="...tabular-nums...">$X</span>...<span...>$Y</span>
    pairs = re.findall(
        r'<span[^>]*class="[^"]*tabular-nums[^"]*"[^>]*>\$([\d.,]+)</span>.*?<span[^>]*class="[^"]*tabular-nums[^"]*"[^>]*>\$([\d.,]+)</span>',
        html,
        re.DOTALL
    )
    
    if pairs:
        if len(pairs) >= 1:
            data['api_used'] = float(pairs[0][0].replace(',', ''))
            data['api_allowance'] = float(pairs[0][1].replace(',', ''))
            if data['api_allowance'] > 0:
                data['api_usage_percent'] = round(data['api_used'] / data['api_allowance'] * 100, 2)
        
        if len(pairs) >= 2:
            data['vibe_used'] = float(pairs[1][0].replace(',', ''))
            data['vibe_allowance'] = float(pairs[1][1].replace(',', ''))
            if data['vibe_allowance'] > 0:
                data['vibe_usage_percent'] = round(data['vibe_used'] / data['vibe_allowance'] * 100, 2)
    
    return data


def _fetch_subscription_page(cookies: dict[str, str]) -> str | None:
    """Fetch the Mistral subscription page using the provided cookies."""
    url = 'https://admin.mistral.ai/subscription'
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    }
    try:
        import requests
        response = requests.get(url, headers=headers, cookies=cookies, timeout=15)
        response.raise_for_status()
        return response.text
    except Exception:
        return None


def export_mistral_subscription(timeout: float = 30, use_chrome: bool = True, use_saved_cookies: bool = True) -> list[dict[str, Any]]:
    """Get Mistral subscription data for all accounts.
    
    Tries three methods in order:
    1. Saved cookie files (persistent, no Chrome needed)
    2. Chrome cookies via browser_cookie3 (real-time)
    3. OmniRoute container API keys (fallback)
    
    Args:
        timeout: Timeout for subprocess calls (default 30s)
        use_chrome: Whether to try Chrome cookies method (default True). Set to False for tests.
        use_saved_cookies: Whether to try saved cookie files (default True). Set to False for tests.
    
    Returns a list of subscription rows, one per account.
    """
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    results: list[dict[str, Any]] = []
    
    # Method 1: Try saved cookie files (persistent, no Chrome needed)
    if use_saved_cookies:
        try:
            saved_accounts = mistral_cookies.get_all_account_data()
            if saved_accounts:
                for account_data in saved_accounts:
                    account_rows = mistral_cookies.format_for_dashboard(account_data)
                    results.extend(account_rows)
                if results:
                    return results
        except Exception:
            pass  # Fall through to Method 2
    
    # Method 2: Try Chrome cookies (real-time data from browser)
    if use_chrome:
        try:
            accounts = mistral_cookies.capture_account_from_browser()
            if accounts:
                if isinstance(accounts, list):
                    for account_data in accounts:
                        results.append(mistral_cookies.format_for_dashboard(account_data))
                else:
                    results.append(mistral_cookies.format_for_dashboard(accounts))
                if results:
                    return results
        except Exception:
            pass  # Fall through to Method 3
    
    # Method 3: Try OmniRoute container (API keys)
    container_available = os.environ.get("OMNIROUTE_USAGE_CONTAINER") or _container_exists()
    if container_available:
        try:
            result = subprocess.run(
                ["docker", "exec", "-i", CONTAINER, "node", "-e", _NODE_SCRIPT],
                capture_output=True, text=True, timeout=timeout)
            if result.returncode == 0:
                accounts = json.loads(result.stdout.strip().splitlines()[-1])
                for acc in accounts:
                    email = acc.get("email") or acc.get("account")
                    plan = acc.get("plan") or "unknown"
                    results.append({
                        "provider": "mistral_subscription",
                        "account": acc.get("account"),
                        "account_label": email,
                        "label": f"Mistral {plan}",
                        "plan_type": plan,
                        "status": "ok" if acc.get("me_status") == 200 else "unavailable",
                        "fetched_at": fetched_at,
                        "windows": [{
                            "label": f"Plan: {plan}",
                            "used_percentage": None,
                            "status": "ok" if acc.get("me_status") == 200 else "unavailable",
                            "detail": {
                                "email": email, "plan": plan,
                                "payg_enabled": acc.get("payg_enabled"),
                                "vibe_payg_enabled": acc.get("vibe_payg_enabled"),
                                "workspace_id": acc.get("workspace_id"),
                                "organization_id": acc.get("organization_id"),
                            },
                        }],
                    })
                if results:
                    return results
            else:
                # Container exists but command failed
                results.append({
                    "provider": "mistral_subscription",
                    "label": "Mistral subscription",
                    "status": "unavailable",
                    "fetched_at": fetched_at,
                    "detail": "OmniRoute container command failed."
                })
                return results
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            results.append({
                "provider": "mistral_subscription",
                "label": "Mistral subscription",
                "status": "unavailable",
                "fetched_at": fetched_at,
                "detail": f"Mistral account read failed: {type(exc).__name__}"
            })
            return results
    
    # If neither method worked, return not_configured
    results.append({
        "provider": "mistral_subscription",
        "label": "Mistral subscription",
        "status": "not_configured",
        "fetched_at": fetched_at,
        "detail": "No Mistral accounts configured."
    })
    
    return results
