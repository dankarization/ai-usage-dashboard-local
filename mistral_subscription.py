"""Mistral subscription allowances per account, read via the API keys stored
(encrypted) in the local OmniRoute container's storage.sqlite.

Keys are decrypted inside the container (scrypt + AES-256-GCM, same scheme as
OmniRoute itself) and only ever used for HTTP calls; they are never printed,
logged, committed or persisted by this module.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

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


def export_mistral_subscription(timeout: float = 30) -> list[dict[str, Any]]:
    """One QuotaSnapshot-like row per Mistral account reachable via OmniRoute keys."""
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if not os.environ.get("OMNIROUTE_USAGE_CONTAINER") and not _container_exists():
        return [{"provider": "mistral_subscription", "label": "Mistral subscription",
                 "status": "not_configured", "fetched_at": fetched_at,
                 "detail": "OMNIROUTE_USAGE_CONTAINER not set; Mistral keys unavailable."}]
    try:
        result = subprocess.run(
            ["docker", "exec", "-i", CONTAINER, "node", "-e", _NODE_SCRIPT],
            capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return [{"provider": "mistral_subscription", "label": "Mistral subscription",
                     "status": "unavailable", "fetched_at": fetched_at,
                     "detail": "OmniRoute container read failed."}]
        accounts = json.loads(result.stdout.strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return [{"provider": "mistral_subscription", "label": "Mistral subscription",
                 "status": "unavailable", "fetched_at": fetched_at,
                 "detail": "Mistral account read failed: " + type(exc).__name__}]

    rows = []
    for acc in accounts:
        email = acc.get("email") or acc.get("account")
        plan = acc.get("plan") or "unknown"
        rows.append({
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
    if not rows:
        rows.append({"provider": "mistral_subscription", "label": "Mistral subscription",
                     "status": "not_configured", "fetched_at": fetched_at,
                     "detail": "No active Mistral API keys in OmniRoute."})
    return rows


def _container_exists() -> bool:
    try:
        r = subprocess.run(["docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=10)
        return CONTAINER in r.stdout.split()
    except (OSError, subprocess.SubprocessError):
        return False
