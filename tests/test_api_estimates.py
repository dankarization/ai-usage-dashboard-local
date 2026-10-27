"""API-equivalent estimates must cover token categories, not native dollar coverage."""
from copy import deepcopy
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import auto_usage
import openclaw_usage
from pricing_config import MODEL_PRICING, api_pricing, estimate_api_tokens


def tokens(inp=0, out=0, cached=0, written=0):
    return dict(input=inp, output=out, cache_read=cached, cache_write=written,
                total=inp + out + cached + written)


# Fixed category counts: changing native dollars or missing-cost entries must
# never change this independent standard API baseline.
GPT = [
    ('gpt-6.1-sol', tokens(44209018, 4398643, 778164992), 70.879374, 210.2209652),
    ('gpt-6-astra', tokens(2416717, 341370, 43680000), 43.504611, 84.91567),
    ('gpt-6-luna', tokens(5025848, 615081, 91343616), .39362256, 1.72356146),
    ('gpt-6-sol', tokens(749505, 60758, 24188416), .2577716, 6.9442732),
    ('gpt-5.6-sol', tokens(812815, 71467, 8645888), 5.1363116, 8.1389552),
    ('gpt-5.6-terra', tokens(35434, 2654, 117248), .1261656, .1261656),
]


@pytest.mark.parametrize('model,counts,native,expected', GPT)
def test_frozen_gpt_category_estimates(model, counts, native, expected):
    result = estimate_api_tokens(model, counts, 'openai')
    assert result['cost_usd'] == pytest.approx(expected)
    assert result['cost_kind'] == 'estimated'
    assert result['priced_tokens'] == counts['total']
    assert result['unpriced_tokens'] == 0
    assert result['api_pricing']['unit'] == 'million_tokens'
    assert result['api_pricing']['reference'].startswith('https://developers.openai.com/')


def test_frozen_30d_and_7d_prices_do_not_use_aggregate_long_tier():
    assert sum(estimate_api_tokens(m, t)['cost_usd'] for m, t, _, _ in GPT) == pytest.approx(312.06959066)
    assert estimate_api_tokens('gpt-6-sol', tokens(55293299, 5105663, 1207039232))['cost_usd'] == pytest.approx(403.0510744)
    # Many short requests can sum above 272K. Do not select a long tier from this sum.
    assert estimate_api_tokens('gpt-6-sol', tokens(160746, 18832, 10599680))['cost_usd'] == pytest.approx(2.629748)


def test_all_existing_exact_rates_are_preserved():
    for model, price in MODEL_PRICING.items():
        if model == 'gpt-5.4-mini-fast':
            # Current official API table supersedes the legacy exporter alias.
            assert api_pricing(model)[1] == dict(input=1.5, output=9.0, cached=.15)
        else:
            assert api_pricing(model)[1] == price, model
    assert api_pricing('openai/gpt-6-sol-900k') == api_pricing('gpt-6-sol')
    assert api_pricing('qwen/qwen3.8-27b')[1]['input'] == .42
    assert api_pricing('claude-opus-invented')[1] is None
    assert api_pricing('gpt-6.1-sol-pro')[1] is None
    assert api_pricing('gpt-5.5-fast')[1] == dict(input=12.5, output=75, cached=1.25)
    assert api_pricing('gpt-6-sol-fast')[1]['input'] == 4
    assert api_pricing('gpt-invented-fast')[1] is None


def test_disjoint_cache_categories_no_second_subtraction_and_no_inferred_rates():
    assert estimate_api_tokens('gpt-6-astra', tokens(1000000, 1000000, 1000000, 1000000))['cost_usd'] == 73.5
    partial = estimate_api_tokens('gemini-3-flash', tokens(1000000, 1000000, 1000000))
    assert partial['cost_usd'] == 3.5
    assert partial['cost_kind'] == 'partial'
    assert partial['priced_tokens'] == 2000000 and partial['unpriced_tokens'] == 1000000
    assert 'cache_read' in partial['unpriced_categories']
    unknown_categories = estimate_api_tokens('gpt-6-sol', {'input': 1000, 'total': 1500})
    assert unknown_categories['cost_usd'] == .002
    assert unknown_categories['unpriced_tokens'] == 500
    assert unknown_categories['cost_kind'] == 'partial'
    assert estimate_api_tokens('gpt-6-sol', {'total': 1500})['cost_usd'] is None
    assert estimate_api_tokens('gpt-6-sol', dict(tokens(10), total=1))['cost_usd'] is None


def test_unknown_and_real_free_are_distinct():
    assert estimate_api_tokens('unknown-model', tokens(100))['cost_usd'] is None
    assert estimate_api_tokens('nr-custom-bucket', tokens(100), 'omniroute')['cost_usd'] is None
    free = estimate_api_tokens('local-model', {'total': 100}, 'local')
    assert free['cost_usd'] == 0 and free['priced_tokens'] == 100 and free['unpriced_tokens'] == 0
    assert estimate_api_tokens('qwen/qwen3.8-27b:free', tokens(100))['cost_usd'] == 0


@pytest.fixture
def category_payload(monkeypatch):
    rows = []
    for model, counts, native, _ in GPT:
        values = dict(zip(('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens'),
                          (counts[k] for k in ('input', 'output', 'cache_read', 'cache_write', 'total'))))
        rows.append(dict(provider='openai', model=model,
                         totals=dict(values, totalCost=native, missingCostEntries=100)))
    # Positive stored cost is not a verified tariff, just as zero is not proof of free usage.
    rows += [dict(provider='google', model='gemini-3-flash', totals=dict(input=1000000, output=1000000, cacheRead=1000000, cacheWrite=0, totalTokens=3000000, totalCost=0)),
             dict(provider='custom', model='unknown-model', totals=dict(input=10, totalTokens=10, totalCost=90)),
             dict(provider='local', model='local-model', totals=dict(totalTokens=100, totalCost=0))]
    aggregate = {'aggregates': {'byModel': rows, 'modelDaily': [
        dict(provider='openai', model=GPT[0][0], date='2026-10-21', tokens=GPT[0][1]['total'], cost=1)]}}
    frozen = deepcopy(aggregate)
    monkeypatch.setattr(openclaw_usage, 'load_with_cache', lambda *_: dict(
        response=frozen, status='ok', cached_at_utc=None, error=None, gateway_cache_status='fresh'))
    monkeypatch.setattr(auto_usage, 'get_date_range', lambda _: ('2026-10-21', '2026-10-27', None, None))
    monkeypatch.setattr(auto_usage._hermes_usage, 'state_dbs', lambda: ['fixture'])
    hermes = dict(source='hermes', provider='openai', model='gpt-6-sol-900k',
                  totals=tokens(1000000, 1000000, 1000000), daily=[dict(date='2026-10-21', **tokens(1000000, 1000000, 1000000))],
                  cost_usd=0.01, cost_kind='partial')
    monkeypatch.setattr(auto_usage._hermes_usage, 'model_entries', lambda *a, **kw: [deepcopy(hermes)])
    monkeypatch.setattr(auto_usage._omniroute_usage, 'collect', lambda *a, **kw: dict(
        models=[], daily=[], status='ok', source=dict(tokens=0, complete=True, additive=False)))
    monkeypatch.delenv('NORDROUTER_API_KEY', raising=False)
    result = auto_usage.build_model_breakdown(7)
    assert aggregate == frozen  # projection never rewrites the source snapshot
    return result


def test_mixed_source_coverage_and_daily_semantics(category_payload):
    p = category_payload
    assert p['sources']['openclaw']['estimated_cost_usd'] == pytest.approx(312.06959066 + 3.5)
    assert p['sources']['openclaw']['unpriced_models'] == 2
    assert p['sources']['openclaw']['partial_models'] == 1
    assert p['sources']['openclaw']['priced_models'] == 8
    assert p['sources']['openclaw']['unpriced_tokens'] == 1000010
    assert p['sources']['hermes']['estimated_cost_usd'] == 12.2
    hermes = next(m for m in p['models'] if m['source'] == 'hermes')
    assert hermes['daily'][0]['cost_usd'] == 12.2
    gateway = next(m for m in p['models'] if m['source'] == 'openclaw' and m['model'] == GPT[0][0])
    assert gateway['daily'][0]['total'] == GPT[0][1]['total']
    assert gateway['daily'][0]['cost_usd'] is None  # no categories, no fabricated daily dollar split
    assert gateway['cost_usd'] == pytest.approx(GPT[0][3])


def test_api_serializes_coverage_and_rendered_ui_matches_server(category_payload, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    import local_display_service as service
    from dashboard_page import DASHBOARD_HTML
    monkeypatch.setattr(service, 'load_env', lambda: None)
    monkeypatch.setattr(service, 'build_model_breakdown', lambda **kw: category_payload)
    response = TestClient(service.app).get('/api/v1/model-breakdown?days=7')
    assert response.status_code == 200
    body = response.json()
    assert body['sources'] == category_payload['sources']
    row = next(m for m in body['models'] if m['model'] == 'gpt-6.1-sol')
    assert row['api_pricing']['rates']['cached'] == .1
    assert row['priced_tokens'] == GPT[0][1]['total']
    if shutil.which('node') is None:
        pytest.skip('Node required for rendered DOM proof')
    script = tmp_path / 'dashboard.js'
    script.write_text(re.search(r'<script>(.*?)</script>', DASHBOARD_HTML, re.S).group(1))
    payload = tmp_path / 'api.json'
    payload.write_text(json.dumps(body))
    harness = Path(__file__).with_name('dashboard_dom_harness.js')
    run = subprocess.run(['node', str(harness), str(script), str(payload)], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stdout + run.stderr
    assert 'server/API/hero/model rows agree' in run.stdout
