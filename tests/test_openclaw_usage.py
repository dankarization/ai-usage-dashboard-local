"""Gateway aggregation boundaries for dashboard history."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openclaw_usage
from auto_usage import build_model_breakdown


def test_openclaw_model_projection_includes_comparison_route_without_account():
    response = {'aggregates': {
        'byModel': [
            {'provider': 'openai', 'model': 'gpt-test', 'totals': {
                'input': 4, 'output': 3, 'cacheRead': 2, 'cacheWrite': 1,
                'totalTokens': 10}},
            {'provider': 'nordrouter', 'model': 'gpt-test', 'totals': {'totalTokens': 20}},
        ],
        'modelDaily': [
            {'date': '2026-10-07', 'provider': 'openai', 'model': 'gpt-test',
             'tokens': 10, 'cost': 0.03},
            {'date': '2026-10-07', 'provider': 'nordrouter', 'model': 'gpt-test',
             'tokens': 20, 'cost': 0.04},
        ],
    }}
    rows = openclaw_usage.model_entries(response, include_daily=True)
    assert len(rows) == 2
    assert rows[0]['source'] == 'openclaw'
    assert rows[0]['provider'] == 'openai'
    assert all('account' not in row for row in rows)
    assert rows[0]['totals'] == {'input': 4, 'output': 3, 'cache_read': 2,
                                  'cache_write': 1, 'total': 10}
    assert rows[0]['daily'][0]['input'] is None
    assert rows[0]['daily'][0]['total'] == 10
    assert rows[1]['provider'] == 'nordrouter'
    assert rows[1]['daily'][0]['total'] == 20


def test_breakdown_uses_gateway_and_direct_nordrouter_only(monkeypatch):
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *_: {'aggregates': {
        'byModel': [{'provider': 'xai', 'model': 'grok-test',
                     'totals': {'input': 5, 'output': 3, 'cacheRead': 2,
                                'cacheWrite': 0, 'totalTokens': 10, 'totalCost': 0.2}},
                    {'provider': 'local', 'model': 'local-test',
                     'totals': {'totalTokens': 2}},
                    {'provider': 'nordrouter', 'model': 'nr-model',
                     'totals': {'totalTokens': 21, 'totalCost': 0.5}}],
        'modelDaily': [{'date': '2026-10-07', 'provider': 'xai',
                        'model': 'grok-test', 'tokens': 10, 'cost': 0.2},
                       {'date': '2026-10-07', 'provider': 'nordrouter',
                        'model': 'nr-model', 'tokens': 21, 'cost': 0.5}],
    }})
    class DirectClient:
        def __init__(self, key):
            assert key == 'fixture-key'
        def get(self, endpoint, **params):
            assert endpoint == 'analytics'
            return {'window_days': 7, 'totals': {'amount_usd': 0.4},
                    'daily': [{'date': f'2026-10-0{day}', 'tokens': 20 if day == 1 else 0,
                               'amount_usd': 0.4 if day == 1 else 0} for day in range(1, 8)],
                    'top_models': [{'id': 'nr-model', 'tokens': 20,
                                    'amount_usd': 0.4}]}, False
    monkeypatch.setenv('NORDROUTER_API_KEY', 'fixture-key')
    monkeypatch.setattr('auto_usage.get_date_range', lambda days: ('2026-10-01', '2026-10-07', None, None))
    monkeypatch.setattr('auto_usage._nordrouter_usage.Client', DirectClient)
    result = build_model_breakdown(days=7)
    assert [(r['source'], r['provider'], r['totals']['total']) for r in result['models']] == [
        ('openclaw', 'nordrouter', 21), ('nordrouter', 'nordrouter', 20),
        ('openclaw', 'xai', 10), ('openclaw', 'local', 2)]
    assert all('account' not in row for row in result['models'])
    assert result['source_daily']['nordrouter'][0]['tokens'] == 20
    assert result['totals']['total'] == 32
    assert result['sources']['openclaw']['estimated_cost_usd'] == 0.2
    assert result['sources']['openclaw']['unpriced_models'] == 1
    assert result['sources']['nordrouter']['billed_cost_usd'] == 0.4
    assert result['sources']['nordrouter']['complete'] is True
    assert result['sources']['openclaw_nordrouter_comparison_tokens'] == 21

    class PartialClient(DirectClient):
        def get(self, endpoint, **params):
            data, stale = super().get(endpoint, **params)
            return {**data, 'daily': data['daily'][:-1]}, stale

    monkeypatch.setattr('auto_usage._nordrouter_usage.Client', PartialClient)
    partial = build_model_breakdown(days=7)
    assert partial['sources']['nordrouter']['complete'] is False
    assert partial['totals']['total'] is None
