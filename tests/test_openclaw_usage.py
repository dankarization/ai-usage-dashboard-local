"""Gateway aggregation boundaries for dashboard history."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import openclaw_usage
from auto_usage import build_model_breakdown


def test_openclaw_model_projection_excludes_nordrouter_and_unknown_account():
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
    assert len(rows) == 1
    assert rows[0]['source'] == 'openclaw'
    assert rows[0]['provider'] == 'openai'
    assert rows[0]['account'] == 'unknown'
    assert rows[0]['totals'] == {'input': 4, 'output': 3, 'cache_read': 2,
                                  'cache_write': 1, 'total': 10}
    assert rows[0]['daily'][0]['input'] is None
    assert rows[0]['daily'][0]['total'] == 10


def test_breakdown_uses_gateway_and_direct_nordrouter_only(monkeypatch):
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *_: {'aggregates': {
        'byModel': [{'provider': 'xai', 'model': 'grok-test',
                     'totals': {'input': 5, 'output': 3, 'cacheRead': 2,
                                'cacheWrite': 0, 'totalTokens': 10}}],
        'modelDaily': [{'date': '2026-10-07', 'provider': 'xai',
                        'model': 'grok-test', 'tokens': 10, 'cost': 0.2}],
    }})
    class DirectClient:
        def __init__(self, key):
            assert key == 'fixture-key'
        def get(self, endpoint, **params):
            assert endpoint == 'analytics'
            return {'daily': [{'date': '2026-10-07', 'tokens': 20,
                               'amount_usd': 0.4}],
                    'top_models': [{'id': 'nr-model', 'tokens': 20,
                                    'amount_usd': 0.4}]}, False
    monkeypatch.setenv('NORDROUTER_API_KEY', 'fixture-key')
    monkeypatch.setattr('auto_usage._nordrouter_usage.Client', DirectClient)
    result = build_model_breakdown(days=7)
    assert [(r['source'], r['provider'], r['totals']['total']) for r in result['models']] == [
        ('nordrouter', 'nordrouter', 20), ('openclaw', 'xai', 10)]
    assert result['source_daily']['nordrouter'][0]['tokens'] == 20
    assert result['totals']['total'] == 30
