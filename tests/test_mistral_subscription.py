import json
import subprocess
from unittest.mock import patch

import mistral_subscription as provider


def test_not_configured_without_container(monkeypatch):
    monkeypatch.delenv("OMNIROUTE_USAGE_CONTAINER", raising=False)
    monkeypatch.setattr(provider, "_container_exists", lambda: False)
    rows = provider.export_mistral_subscription()
    assert rows[0]["provider"] == "mistral_subscription"
    assert rows[0]["status"] == "not_configured"


def test_parses_accounts_from_container_output(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_USAGE_CONTAINER", "omniroute")
    payload = [
        {"account": "main", "email": "a@b.c", "workspace_id": "ws1", "organization_id": "org1",
         "plan": "INDIVIDUAL", "payg_enabled": False, "vibe_payg_enabled": False,
         "me_status": 200, "sub_status": 200},
        {"account": "free1", "email": "d@e.f", "plan": "FREE", "payg_enabled": False,
         "vibe_payg_enabled": False, "me_status": 200, "sub_status": 200},
    ]
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(payload) + "\n", stderr="")
    monkeypatch.setattr(provider.subprocess, "run", lambda *a, **k: completed)
    rows = provider.export_mistral_subscription()
    assert len(rows) == 2
    assert rows[0]["account_label"] == "a@b.c"
    assert rows[0]["plan_type"] == "INDIVIDUAL"
    assert rows[0]["status"] == "ok"
    assert rows[1]["plan_type"] == "FREE"
    # no secret material in output
    blob = json.dumps(rows)
    assert "api_key" not in blob and "enc:v1" not in blob


def test_container_failure_is_unavailable(monkeypatch):
    monkeypatch.setenv("OMNIROUTE_USAGE_CONTAINER", "omniroute")
    completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr(provider.subprocess, "run", lambda *a, **k: completed)
    rows = provider.export_mistral_subscription()
    assert rows[0]["status"] == "unavailable"
