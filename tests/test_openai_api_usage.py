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


def test_private_session_file_permissions_and_org(monkeypatch, tmp_path):
    path = tmp_path / 'session.json'
    path.write_text(json.dumps({'session_key': 'session-test-only', 'organization_id': 'org-test',
                                'organization_name': 'Example', 'unexpected': 'never forward'}))
    monkeypatch.setenv('OPENAI_BILLING_SESSION_FILE', str(path))
    monkeypatch.delenv('OPENAI_BILLING_SESSION_KEY', raising=False)
    path.chmod(0o644)
    assert provider._billing_session() == {}
    path.chmod(0o600)
    assert provider._billing_session() == {'session_key': 'session-test-only',
                                         'organization_id': 'org-test', 'organization_name': 'Example'}
    link = tmp_path / 'session-link.json'
    link.symlink_to(path)
    monkeypatch.setenv('OPENAI_BILLING_SESSION_FILE', str(link))
    assert provider._billing_session() == {}


def test_purchased_balance_is_authoritative_not_subsidy_inference(monkeypatch, credential):
    payload = {'total_granted': 105, 'total_used': 34, 'total_available': 71,
               'total_paid_available': 6}
    monkeypatch.setattr(provider, 'urlopen', lambda *a, **k: io.StringIO(json.dumps(payload)))
    row = provider.export_openai_api_quota()[0]
    assert row['billing']['purchased_balance_usd'] == 6
    assert row['billing']['unclassified_free_remaining_usd'] == 65
    assert row['billing']['subsidy_total_usd'] is None
    assert row['billing']['paid_usage_usd'] is None


def test_session_request_keeps_secret_out_of_output(monkeypatch):
    monkeypatch.setattr(provider, '_billing_session', lambda: {
        'session_key': 'private-session-test', 'organization_id': 'org-example',
        'organization_name': 'Example'})
    def respond(request, **kwargs):
        assert request.get_header('Authorization') == 'Bearer private-session-test'
        assert request.get_header('Openai-organization') == 'org-example'
        return io.StringIO(json.dumps({'total_granted': 1, 'total_used': 0, 'total_available': 1,
                                      'total_paid_available': 0}))
    monkeypatch.setattr(provider, 'urlopen', respond)
    row = provider.export_openai_api_quota()[0]
    assert row['billing']['purchased_balance_usd'] == 0
    assert row['billing']['organization_name'] == 'Example'
    assert 'private-session-test' not in json.dumps(row)


def test_grant_dates_are_not_subscription_reset_or_funding_identity(monkeypatch, credential):
    payload = {'total_granted': 100, 'total_used': 25, 'total_available': 75,
               'total_paid_available': 10, 'grants': {'data': [
                   {'id': 'private-grant-identity', 'grant_amount': 100, 'used_amount': 25,
                    'effective_at': 1793491200, 'expires_at': 1796083200, 'is_scheduled': False}]}}
    monkeypatch.setattr(provider, 'urlopen', lambda *a, **k: io.StringIO(json.dumps(payload)))
    row = provider.export_openai_api_quota()[0]
    assert row['used_percentage'] == 25
    assert row['label'] == 'All credits (funding unclassified)'
    grant = row['billing']['grants'][0]
    assert grant['remaining_usd'] == 75
    assert grant['effective_at'] == '2026-11-01T00:00:00+00:00'
    assert grant['expires_at'] == '2026-12-01T00:00:00+00:00'
    assert grant['funding'] == 'unclassified'
    assert row['billing']['cycle_end'] is None
    assert row['billing']['subsidy_used_usd'] is None
    assert 'private-grant-identity' not in json.dumps(row)


@pytest.mark.parametrize('paid', [True, -1, 101, float('nan')])
def test_invalid_purchased_balance_fails_closed(monkeypatch, credential, paid):
    payload = {'total_granted': 100, 'total_used': 0, 'total_available': 100,
               'total_paid_available': paid}
    monkeypatch.setattr(provider, 'urlopen', lambda *a, **k: io.StringIO(json.dumps(payload)))
    row = provider.export_openai_api_quota()[0]
    assert row['status'] == 'unavailable'
    assert row['billing']['purchased_balance_usd'] is None


def test_subscription_entitlement_is_not_prorated_grant_or_reset(monkeypatch, credential):
    monkeypatch.setattr(provider, '_billing_session', lambda: {
        'session_key': 'private-test', 'benefit_period_amount_usd': '100.00',
        'benefit_cadence': 'monthly', 'benefit_expires_at': '2020-01-21T19:23:47Z',
        'benefit_checked_at': '2020-01-01T00:00:00Z'})
    monkeypatch.setattr(provider, 'urlopen', lambda *a, **k: io.StringIO(json.dumps({
        'total_granted': 65, 'total_used': 1, 'total_available': 64, 'total_paid_available': 0})))
    row = provider.export_openai_api_quota()[0]
    assert row['billing']['benefit_period_amount_usd'] == 100
    assert row['billing']['benefit_snapshot_expired'] is True
    assert row['billing']['subsidy_total_usd'] is None
    assert row['billing']['cycle_start'] is None
    assert row['billing']['cycle_end'] is None
    assert row['billing']['aggregate_total_usd'] == 65
