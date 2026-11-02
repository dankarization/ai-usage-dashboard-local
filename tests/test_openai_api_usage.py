"""OpenAI billing stays separate from gross token usage and subscription quotas."""
import io
import json
from urllib.error import HTTPError, URLError

import pytest
import openai_api_usage as provider
import local_display_service as service


def test_aggregate_grants_do_not_imply_subsidy_or_cycle():
    parsed = provider.parse_credit_grants({
        "total_granted": 100, "total_used": 25, "total_available": 75,
        "grants": {"data": [{"effective_at": 1793491200, "expires_at": 1796083200}]},
    })
    assert parsed["aggregate_remaining_usd"] == 75
    assert parsed["aggregate_grant_expiries"] == ["2026-12-01T00:00:00+00:00"]
    assert "subsidy_total_usd" not in parsed
    assert "cycle_end" not in parsed


@pytest.mark.parametrize("data", [
    {}, [], {"total_granted": 100, "total_used": 10, "total_available": 95},
    {"total_granted": True, "total_used": 0, "total_available": 1},
    {"total_granted": float("nan"), "total_used": 0, "total_available": 1},
    {"total_granted": 1, "total_used": -1, "total_available": 2},
    {"total_granted": 1, "total_used": 0, "total_available": 1,
     "grants": {"data": [{"expires_at": "tomorrow"}]}},
])
def test_invalid_or_partial_billing_fails_closed(data):
    with pytest.raises(ValueError):
        provider.parse_credit_grants(data)


@pytest.fixture
def credential(monkeypatch):
    monkeypatch.delenv("OPENAI_BILLING_SESSION_KEY", raising=False)
    monkeypatch.setattr(provider, "_inference_key", lambda: "test-secret")


def test_auth_blocker_never_exposes_error_body_or_fake_zero(monkeypatch, credential):
    def denied(*args, **kwargs):
        raise HTTPError("url", 403, "denied", {}, io.BytesIO(b"secret-key-in-error"))
    monkeypatch.setattr(provider, "urlopen", denied)
    row = provider.export_openai_api_quota()[0]
    assert row["status"] == "unavailable"
    assert row["billing"]["http_status"] == 403
    assert row["billing"]["subsidy_total_usd"] is None
    assert row["billing"]["paid_usage_usd"] is None
    assert row["billing"]["cycle_end"] is None
    assert "percentage" not in row
    assert "secret" not in json.dumps(row)
    assert "Platform session" in row["billing"]["detail"]


def test_no_credential_and_network_error(monkeypatch, credential):
    monkeypatch.setattr(provider, "_inference_key", lambda: "")
    assert "No OpenAI credential" in provider.export_openai_api_quota()[0]["billing"]["detail"]
    monkeypatch.setattr(provider, "_inference_key", lambda: "test")
    monkeypatch.setattr(provider, "urlopen", lambda *a, **k: (_ for _ in ()).throw(URLError("private detail")))
    row = provider.export_openai_api_quota()[0]
    assert "unreachable" in row["billing"]["detail"]
    assert "private detail" not in json.dumps(row)


def test_success_is_partial_not_funding_allocation(monkeypatch, credential):
    payload = {"total_granted": 100, "total_used": 0, "total_available": 100}
    monkeypatch.setattr(provider, "urlopen", lambda *a, **k: io.StringIO(json.dumps(payload)))
    row = provider.export_openai_api_quota()[0]
    assert row["status"] == "partial"
    assert row["billing"]["aggregate_used_usd"] == 0
    assert row["billing"]["subsidy_used_usd"] is None
    assert row["billing"]["non_subsidized_tokens"] is None
    assert row["billing"]["expires_at"] is None


def test_live_quota_route_preserves_billing_unknowns(monkeypatch):
    expected = [{"provider": "openai_api", "label": "API subsidy",
                 "status": "unavailable", "billing": {"subsidy_total_usd": None, "http_status": 403}}]
    monkeypatch.setattr(service, "load_env", lambda: None)
    monkeypatch.setattr(provider, "export_openai_api_quota", lambda: expected)
    result = service.quotas("openai_api")
    assert len(result["quotas"]) == 1
    assert result["quotas"][0]["billing"] == expected[0]["billing"]
    assert result["quotas"][0]["used_percentage"] is None
