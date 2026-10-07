"""Network-free provider and account-isolation regression coverage."""
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
import requests
from fastapi.testclient import TestClient

import auto_usage as usage
import local_display_service as service
import nordrouter_usage as nr


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    for name in ("NORDROUTER_API_KEY", "CODEX_HOME", "CODEX_HOME_2", "CODEX_LABEL_1", "CODEX_LABEL_2"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(usage, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(nr, "CACHE_DIR", tmp_path / "nr")
    def no_network(*args, **kwargs):
        raise AssertionError("Unexpected network request")
    monkeypatch.setattr(requests, "get", no_network)


class Response:
    def __init__(self, body):
        self.body = body
    def raise_for_status(self):
        pass
    def json(self):
        return self.body


def analytics(days=30):
    return {"window_days": days, "totals": {"amount_usd": 9},
            "daily": [{"date": "2026-10-02", "tokens": 123, "amount_usd": 2}],
            "top_models": [{"id": "model-a", "tokens": 123, "amount_usd": 2}]}


def test_cache_ttl_stale_error_backoff_and_secret_filter(monkeypatch, tmp_path):
    clock = [1000]
    monkeypatch.setattr(nr.time, "time", lambda: clock[0])
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            raise requests.HTTPError("rate limited")
        return Response({"balance_usd": 12, "currency": "USD", "key_prefix": "private"})
    monkeypatch.setattr(requests, "get", get)
    client = nr.Client("fake-secret", tmp_path)
    assert client.get("balance") == ({"balance_usd": 12.0, "currency": "USD"}, False)
    clock[0] += 719
    assert client.get("balance")[1] is False
    assert len(calls) == 1
    clock[0] += 1
    assert client.get("balance")[1] is True
    assert client.get("balance")[0]["balance_usd"] == 12
    assert len(calls) == 2
    contents = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert "fake-secret" not in contents and "private" not in contents


def test_cache_isolates_keys_and_limits_query_variants(monkeypatch, tmp_path):
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs)
        return Response(analytics())
    monkeypatch.setattr(requests, "get", get)
    client = nr.Client("fake-one", tmp_path)
    for day in range(1, 10):
        client.get("analytics", days=day)
    assert len(calls) == 4
    nr.Client("fake-two", tmp_path).get("analytics", days=1)
    assert len(calls) == 5


def test_usage_ttl_and_sanitization(monkeypatch, tmp_path):
    clock = [1000]
    monkeypatch.setattr(nr.time, "time", lambda: clock[0])
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs)
        return Response({"data": [{"created_at": "2026-10-01T21:00:00Z",
            "model": "fake-model", "total_tokens": 10, "cost_usd": 0.1,
            "client_ip": "private", "client": "private"}], "has_more": False})
    monkeypatch.setattr(requests, "get", get)
    client = nr.Client("fake-key", tmp_path)
    body, _ = client.get("usage", days=2)
    assert "client_ip" not in body["data"][0]
    clock[0] += 299
    client.get("usage", days=2)
    assert len(calls) == 1
    clock[0] += 1
    client.get("usage", days=2)
    assert len(calls) == 2


def test_local_today_ranking_not_rolling_totals(monkeypatch):
    def get(self, endpoint, **params):
        if endpoint == "balance":
            return {"balance_usd": 7, "currency": "USD"}, False
        if endpoint == "analytics":
            return analytics(params["days"]), False
        return {"data": [
            {"created_at": "2026-10-01T21:00:00Z", "model": "local-today", "total_tokens": 12, "cost_usd": .5},
            {"created_at": "2026-10-01T19:00:00Z", "model": "yesterday", "total_tokens": 20, "cost_usd": 6},
        ], "has_more": True}, False
    monkeypatch.setattr(nr.Client, "get", get)
    summary = nr.collect(key="fake", now=datetime(2026, 10, 2, 3, tzinfo=timezone.utc))
    assert summary["spend_today_usd"] == .5
    assert summary["top_models_today"][0]["id"] == "local-today"
    assert summary["today_complete"] is True
    assert summary["spend_7d_usd"] == 9
    assert "daily" not in nr.quota(summary)


def test_partial_usage_is_explicit_and_bounded(monkeypatch):
    pages = []
    def get(self, endpoint, **params):
        if endpoint == "balance":
            return None, True
        if endpoint == "analytics":
            return analytics(), False
        pages.append(params["page"])
        return {"data": [{"created_at": "2026-10-02T01:00:00Z",
            "model": "fake", "total_tokens": 1, "cost_usd": .1}], "has_more": True}, False
    monkeypatch.setattr(nr.Client, "get", get)
    summary = nr.collect(key="fake", now=datetime(2026, 10, 2, 3, tzinfo=timezone.utc))
    assert len(pages) == 8
    assert summary["today_complete"] is False
    assert summary["spend_today_usd"] == 2
    assert summary["status"] == "stale"


def test_dual_auth_headers_labels_and_resets(monkeypatch, tmp_path):
    for i in (1, 2):
        home = tmp_path / str(i)
        home.mkdir()
        (home / "auth.json").write_text(json.dumps({"tokens": {"access_token": f"fake-{i}", "account_id": f"acct-{i}"}}))
        monkeypatch.setenv("CODEX_HOME" if i == 1 else "CODEX_HOME_2", str(home))
    monkeypatch.setenv("CODEX_LABEL_2", "Work")
    seen = []
    def get(url, **kwargs):
        seen.append(kwargs["headers"])
        return Response({"rate_limit": {"primary_window": {"used_percent": len(seen) * 10,
                            "reset_at": 1800000000, "limit_window_seconds": 18000}}})
    monkeypatch.setattr(requests, "get", get)
    result = usage.load_all_codex_quotas()
    assert [x["account"] for x in result] == ["codex_1", "codex_2"]
    assert [x["percentage"] for x in result] == [10, 20]
    assert result[1]["label"] == "5h"
    assert result[1]["account_label"] == "Work"
    assert result[1]["next_reset_time_ms"] == 1800000000000
    assert [x["Authorization"] for x in seen] == ["Bearer fake-1", "Bearer fake-2"]
    assert [x["ChatGPT-Account-Id"] for x in seen] == ["acct-1", "acct-2"]


def test_codex_profile_display_name_reads_only_email_claim(tmp_path):
    import base64
    home = tmp_path / "codex"
    home.mkdir()
    payload = base64.urlsafe_b64encode(json.dumps({"email": "person@example.test"}).encode()).decode().rstrip("=")
    (home / "auth.json").write_text(json.dumps({"tokens": {"id_token": f"header.{payload}.signature"}}))
    assert usage.codex_profile_display_name(home, "Fallback") == "person@example.test"
    (home / "auth.json").write_text(json.dumps({"tokens": {"id_token": "malformed"}}))
    assert usage.codex_profile_display_name(home, "Fallback") == "Fallback"


def test_missing_secondary_is_unknown_not_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(usage, "load_codex_quota", lambda **kw: [])
    result = usage.load_all_codex_quotas()
    assert result[1]["status"] == "not_configured"
    assert "percentage" not in result[1]
    assert "0% used" not in usage.format_quotas_block(result)


def test_dual_usage_cost_aggregation_and_duplicate_home(monkeypatch, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(first))
    monkeypatch.setenv("CODEX_HOME_2", str(second))
    homes = []
    def export(start, home):
        homes.append(home)
        return {"daily": [{"date": "2026-10-02", "totalTokens": 20,
            "models": {"fake-model": {"inputTokens": 1000000}}}]}
    monkeypatch.setattr(usage, "_export_codex_profile", export)
    monkeypatch.setattr(usage, "get_pricing", lambda model: {"input": 1, "output": 1, "cached": 1})
    monkeypatch.setattr(usage, "calc_cost", lambda *a, **kw: 1.5)
    usage.export_codex("2026-10-01")
    assert homes == [first, second]
    assert usage.load_codex()[date(2026, 10, 2)] == 40
    assert usage.calc_codex_cost()[date(2026, 10, 2)] == 3
    monkeypatch.setenv("CODEX_HOME_2", str(first))
    usage.export_codex("2026-10-01")
    assert usage.load_codex()[date(2026, 10, 2)] == 20


def test_export_passes_profile_env(monkeypatch, tmp_path):
    seen = []
    class Result:
        returncode = 0
        stdout = '{"daily":[]}'
    def run(*args, **kwargs):
        seen.append(kwargs)
        return Result()
    monkeypatch.setattr(usage.subprocess, "run", run)
    usage._export_codex_profile("2026-10-01", tmp_path)
    assert seen[0]["env"]["CODEX_HOME"] == str(tmp_path)


def test_api_preserves_account_and_nordrouter_fields(monkeypatch):
    monkeypatch.setattr(service, "_cached_payload", {"meta": {}, "quotas": [
        {"provider": "codex", "label": "Primary", "account": "codex_1", "percentage": 15},
        {"provider": "codex", "label": "Secondary", "account": "codex_2", "status": "not_configured"},
        {"provider": "nordrouter", "label": "NordRouter USD", "balance_usd": 7,
         "spend_today_usd": .5, "top_models_today": [{"id": "fake", "amount_usd": .5}], "today_complete": True},
    ]})
    body = TestClient(service.app).get("/api/v1/quotas").json()
    assert body["quotas"][0]["used_percentage"] == 15
    assert body["quotas"][1]["used_percentage"] is None
    assert body["quotas"][1]["remaining_percentage"] is None
    assert body["quotas"][2]["balance_usd"] == 7
    assert body["quotas"][2]["top_models_today"][0]["id"] == "fake"


def test_dashboard_nordrouter_totals_and_column(capsys, tmp_path):
    nr_data = {"daily": [{"date": "2026-10-02", "tokens": 123, "amount_usd": 2}],
               "status": "ok", "today_basis": "Asia/Tbilisi timestamps"}
    result = usage.generate_dashboard(*[{} for _ in range(9)], "2026-10-02", "2026-10-02",
                daily_costs={date(2026, 10, 2): 2}, skip_desktop_chart=True, nordrouter=nr_data)
    assert result["summary"]["categories"]["nordrouter"] == 123
    assert result["summary"]["total_tokens"] == 123
    assert result["summary"]["total_cost_usd"] == 2
    assert "NordRouter" in capsys.readouterr().out


def test_malformed_response_preserves_cache(monkeypatch, tmp_path):
    clock = [1000]
    monkeypatch.setattr(nr.time, 'time', lambda: clock[0])
    body = [analytics()]
    monkeypatch.setattr(requests, 'get', lambda *a, **kw: Response(body[0]))
    client = nr.Client('fake', tmp_path)
    good, stale = client.get('analytics', days=30)
    assert not stale
    body[0] = {'daily': [{'date': 'bad'}]}
    clock[0] += 721
    assert client.get('analytics', days=30) == (good, True)


def test_failed_export_uses_only_its_profile_cache(monkeypatch, tmp_path):
    home = tmp_path / 'first'
    home.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setattr(usage, '_export_codex_profile', lambda *a: {'daily': [{'date': '2026-10-02', 'totalTokens': 5}]})
    usage.export_codex('2026-10-01')
    monkeypatch.setattr(usage, '_export_codex_profile', lambda *a: None)
    usage.export_codex('2026-10-01')
    assert usage.load_codex()[date(2026, 10, 2)] == 5
    other = tmp_path / 'other'
    other.mkdir()
    monkeypatch.setenv('CODEX_HOME', str(other))
    usage.export_codex('2026-10-01')
    assert usage.load_codex() == {}


def test_model_breakdown_includes_nordrouter_cost_without_invented_days(monkeypatch):
    monkeypatch.setenv('NORDROUTER_API_KEY', 'fake')
    for name in ('load_opencode_detailed', 'load_claude_code_detailed', 'load_antigravity_detailed',
                 '_load_cursor_detailed', 'load_glm', 'load_codex'):
        monkeypatch.setattr(usage, name, lambda *a, **kw: {})
    monkeypatch.setattr(usage._dsh_usage, 'load_dsh_detailed', lambda **kw: {})
    monkeypatch.setattr(nr.Client, 'get', lambda *a, **kw: (analytics(), False))
    result = usage.build_model_breakdown(7)
    entry = result['models'][0]
    assert entry['source'] == 'nordrouter'
    assert entry['totals']['total'] == 123
    assert entry['cost_usd'] == 2
    assert entry['daily'] == []
    assert result['totals']['total'] == 123
