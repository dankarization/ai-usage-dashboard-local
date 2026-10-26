"""Serving-attempt accounting, exact tariffs, isolation, and bounded refreshes."""
from copy import deepcopy
from datetime import datetime
import json
import subprocess

import pytest

import omniroute_usage as om
import nordrouter_usage as nr


def route(model='vendor/a:fjord', **values):
    return dict(bucket='parent', provider='nr-route', model=model,
                route_path='parent/child/0', usage_provenance='reported', status=200,
                timestamp='2026-10-22T21:00:00Z', requests=1,
                prompt=1_000_000, output=100_000, cache_read=600_000,
                cache_write=100_000, invalid=0, **values)


def snapshot(rows=None):
    return {'rows': rows if rows is not None else [route()], 'providers': {'nr-route': True},
            'history': {'first': '2026-09-01T00:00:00Z', 'last': '2026-10-23T00:00:00Z'}}


def prices():
    return {'generated_at': '2026-10-23T00:00:00Z', 'data': [
        {'id': 'vendor/a:fjord', 'input_usd': 2, 'output_usd': 10, 'cache_read_usd': 0.1, 'cache_write_usd': 3},
        {'id': 'vendor/a:saga', 'input_usd': 4, 'output_usd': 20, 'cache_read_usd': 0.2, 'cache_write_usd': 6},
        # Unused fallback candidate must not enter the bucket sum.
        {'id': 'vendor/unused', 'input_usd': 999, 'output_usd': 999},
    ]}


def test_actual_fallback_attempts_exact_suffixes_cache_and_calendar_day():
    rows = [route(), route('vendor/a:saga')]
    rows[1]['status'] = 502  # a failed attempt consumed tokens and still costs
    # One more physical request with identical counters/time/path must not be deduped.
    rows.append(deepcopy(rows[0]))
    body = snapshot(rows)
    result = om.aggregate(body, prices(), '2026-10-23', '2026-10-23')
    bucket, = result['models']
    assert bucket['cost_usd'] == pytest.approx(1.96 * 4)
    assert bucket['totals'] == {'input': 900_000, 'output': 300_000,
                                'cache_read': 1_800_000, 'cache_write': 300_000, 'total': 3_300_000}
    routes = bucket['bucket_usage']['routes']
    assert {r['model'] for r in routes} == {'vendor/a:fjord', 'vendor/a:saga'}
    assert sum(r['cost_usd'] for r in routes) == pytest.approx(bucket['cost_usd'])
    assert routes[0]['requests'] == 2
    assert routes[0]['daily'][0]['requests'] == 2
    assert len(routes[0]['daily']) == 1
    assert bucket['daily'][0]['date'] == '2026-10-23'  # UTC 22nd -> local 23rd
    assert bucket['daily'][0]['cost_usd'] == pytest.approx(bucket['cost_usd'])
    assert result['source']['additive'] is False
    assert result['daily'][0]['amount_usd'] == pytest.approx(bucket['cost_usd'])
    assert body == snapshot(rows)  # aggregation is pure


def test_unknown_is_not_zero_no_suffix_fallback_and_retention_is_exposed():
    rows = [route(), route('vendor/a:saga-unknown')]
    body = snapshot(rows)
    body['history']['first'] = '2026-10-23T00:00:00Z'
    result = om.aggregate(body, prices(), '2026-10-23', '2026-10-23')
    bucket, = result['models']
    assert bucket['cost_usd'] is None
    assert bucket['cost_kind'] == 'partial'
    assert bucket['bucket_usage']['known_cost_usd'] == pytest.approx(1.96)
    assert bucket['bucket_usage']['unpriced_requests'] == 1
    assert bucket['bucket_usage']['retention_limited'] is True
    assert result['source']['complete'] is False
    assert bucket['daily'][0]['cost_usd'] is None
    zero = prices()
    zero['data'][0].update(input_usd=0, output_usd=0, cache_read_usd=0, cache_write_usd=0)
    assert om.aggregate(snapshot(), zero, '2026-10-23', '2026-10-23')['models'][0]['cost_usd'] == 0
    assert om.price_id('a', {'vendor/a:fjord': {}, 'vendor/a:saga': {}}) is None
    assert om.price_id('a:fjord', {'vendor/a:fjord': {}}) == 'vendor/a:fjord'
    assert om.price_id('other/a:fjord', {'vendor/a:fjord': {}}) is None


def test_failed_unused_candidates_outer_rows_and_missing_tokens():
    fail = route('unused')
    fail.update(status=429, prompt=0, output=0, cache_read=0, cache_write=0)
    invalid = route()
    invalid.update(invalid=1, prompt=1, cache_read=2)
    result = om.aggregate(snapshot([fail, invalid]), prices(), '2026-10-23', '2026-10-23')
    bucket, = result['models']
    assert len(bucket['bucket_usage']['routes']) == 1
    assert bucket['cost_usd'] is None
    assert bucket['bucket_usage']['routes'][0]['unknown_reason'] == 'invalid_or_missing_usage'
    assert om.aggregate(snapshot([]), prices(), '2026-10-23', '2026-10-23')['models'] == []


def test_renames_nested_paths_and_provider_ambiguity_do_not_invent_history():
    old, new = route(), route()
    old.update(bucket='old-name', route_path='old-name/ref:child/1')
    new.update(bucket='new-name', route_path=None, provider='deleted-connection')
    result = om.aggregate(snapshot([old, new]), prices(), '2026-10-23', '2026-10-23', include_daily=False)
    buckets = {m['model']: m for m in result['models']}
    assert set(buckets) == {'old-name', 'new-name'}
    assert buckets['old-name']['bucket_usage']['routes'][0]['route_path'] == 'old-name/ref:child/1'
    assert buckets['new-name']['cost_usd'] is None
    assert buckets['new-name']['bucket_usage']['routes'][0]['nordrouter'] is None
    assert all(not m['daily'] and not m['bucket_usage']['routes'][0]['daily'] for m in result['models'])


def test_half_open_selected_window_and_dst():
    assert om.bounds('2026-10-23', '2026-10-23') == ('2026-10-22T20:00:00+00:00', '2026-10-23T20:00:00+00:00')
    from zoneinfo import ZoneInfo
    a, b = om.bounds('2026-11-01', '2026-11-01', ZoneInfo('America/New_York'))
    assert (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() == 25 * 3600
    before, inside, after = route(), route(), route()
    before['timestamp'] = '2026-10-22T19:59:59Z'
    inside['timestamp'] = '2026-10-22T20:00:00Z'
    after['timestamp'] = '2026-10-23T20:00:00Z'
    result = om.aggregate(snapshot([before, inside, after]), prices(), '2026-10-23', '2026-10-23')
    assert result['models'][0]['bucket_usage']['priced_requests'] == 1


def test_cache_reuses_snapshot_across_windows_and_backs_off_failures(monkeypatch, tmp_path):
    clock = [1000.0]
    monkeypatch.setattr(om.time, 'time', lambda: clock[0])
    client = om.Client('router', tmp_path)
    calls = []
    def read(start, end):
        calls.append((start, end))
        if len(calls) > 1:
            raise subprocess.TimeoutExpired('docker', 20)
        return snapshot()
    monkeypatch.setattr(client, '_read', read)
    assert client.get('2026-10-23')[1] == 'ok'
    assert client.get('2026-10-23')[1] == 'ok'
    assert len(calls) == 1
    clock[0] += om.TTL + 1
    body, status, fetched = client.get('2026-10-23')
    assert body and status == 'stale' and fetched
    assert client.get('2026-10-23')[1] == 'stale'
    assert len(calls) == 2
    assert client.get('2026-10-24')[1] == 'stale'
    assert om.Client('', tmp_path).get('2026-10-23')[1] == 'not_configured'
    assert om.Client('--unsafe', tmp_path).get('2026-10-23')[1] == 'unavailable'


def test_transport_is_read_only_and_payload_contains_only_bounds(monkeypatch, tmp_path):
    def run(argv, **kwargs):
        assert argv[:5] == ['docker', 'exec', '-i', 'router', 'node']
        assert 'readonly: true' in argv[-1] and 'query_only = ON' in argv[-1]
        assert 'request_summary' not in argv[-1] and 'SELECT *' not in argv[-1]
        assert 'connection_id IS NOT NULL' in argv[-1]  # excludes outer combo logs
        assert set(json.loads(kwargs['input'])) == {'start', 'end'}
        return subprocess.CompletedProcess(argv, 0, json.dumps(snapshot()), '')
    monkeypatch.setattr(om.subprocess, 'run', run)
    assert om.Client('router', tmp_path)._read('2026-10-23', '2026-10-23') == snapshot()


def test_real_sql_counts_serving_attempts_once_not_parent_or_direct_logs():
    import re
    import sqlite3
    script = (om.ROOT / 'scripts' / 'omniroute-aggregate.cjs').read_text()
    sql = re.search(r'db.prepare\(`(SELECT combo_name.*?)`\)\.all\(start, end\)', script, re.S).group(1)
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE call_logs(id PRIMARY KEY, correlation_id, combo_name, provider, model, combo_execution_key, usage_provenance, status, timestamp, connection_id, tokens_in, tokens_out, tokens_cache_read, tokens_cache_creation)')
    values = ['same-correlation', 'parent', 'nr-route', 'vendor/a:fjord', 'parent/child/0',
              'reported', 200, '2026-10-22T21:00:00Z', 'connection', 1_000_000, 100_000, 600_000, 100_000]
    insert = 'INSERT INTO call_logs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)'
    for i in range(2):  # both physical attempts count despite shared correlation
        db.execute(insert, [i, *values])
    parent = list(values)
    parent[8] = None  # outer combo response repeats the serving usage
    db.execute(insert, [2, *parent])
    direct = list(values)
    direct[1] = None  # direct traffic has no bucket, never assigned to one
    db.execute(insert, [3, *direct])
    rows = [dict(row) for row in db.execute(sql, om.bounds('2026-10-23', '2026-10-23'))]
    assert len(rows) == 1 and rows[0]['requests'] == 2
    assert rows[0]['prompt'] == 2_000_000
    result = om.aggregate(snapshot(rows), prices(), '2026-10-23', '2026-10-23')
    assert result['models'][0]['cost_usd'] == pytest.approx(3.92)
    db.close()


def test_pricing_sanitization_exact_ids_and_missing_categories():
    body = {'generated_at': '2026-10-23T00:00:00Z', 'data': [
        {'id': 'v/a:fjord', 'unit': 'USD per 1M tokens', 'billing_scheme': 'tokens',
         'input_usd': 0, 'output_usd': 2, 'cache_read_usd': None, 'cache_write_usd': float('nan'), 'secret': 'not persisted'},
        {'id': 'v/image', 'unit': 'per image', 'billing_scheme': 'image', 'input_usd': 1}]}
    clean = nr._sanitize('pricing', body)
    assert clean['data'] == [{'id': 'v/a:fjord', 'input_usd': 0.0, 'output_usd': 2.0, 'cache_read_usd': None, 'cache_write_usd': None}]
    assert om.route_cost({'input': 2, 'output': 3, 'cache_read': 0, 'cache_write': 0}, clean['data'][0]) == 0.000006
    assert om.route_cost({'input': 2, 'output': 3, 'cache_read': 1, 'cache_write': 0}, clean['data'][0]) is None


def test_integration_preserves_overview_and_direct_nr_separately(monkeypatch):
    import auto_usage as usage
    import openclaw_usage
    monkeypatch.setenv('NORDROUTER_API_KEY', 'fixture')
    monkeypatch.setattr(usage._hermes_usage, 'state_dbs', lambda: [])
    _, today, *_ = usage.get_date_range(1)
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *a: {'aggregates': {'byModel': [
        {'provider': 'omniroute', 'model': 'parent', 'totals': {'totalTokens': 99, 'totalCost': 0}},
        {'provider': 'xai', 'model': 'grok', 'totals': {'totalTokens': 5, 'totalCost': 0.2}}], 'modelDaily': []}})
    monkeypatch.setattr(nr.Client, 'get', lambda *a, **kw: ({'window_days': 1, 'daily': [{'date': today, 'tokens': 1000, 'amount_usd': 3}],
        'totals': {'amount_usd': 3}, 'top_models': [{'id': 'vendor/a:fjord', 'tokens': 1000, 'amount_usd': 3}]}, False))
    observation = om.aggregate(snapshot(), prices(), '2026-10-23', '2026-10-23')
    monkeypatch.setattr(om, 'collect', lambda *a, **kw: deepcopy(observation))
    result = usage.build_model_breakdown(1)
    assert result['totals']['total'] == 1104  # unchanged legacy overview; no router tokens added
    assert result['sources']['openclaw']['estimated_cost_usd'] == 0.2
    assert result['sources']['openclaw']['unpriced_models'] == 0
    bucket = next(m for m in result['models'] if m['source'] == 'openclaw' and m['provider'] == 'omniroute')
    assert bucket['totals']['total'] == 99 and bucket['cost_usd'] is None
    assert bucket['overview_cost_usd'] == 0
    direct = next(m for m in result['models'] if m['source'] == 'nordrouter')
    assert direct['totals']['total'] == 1000 and direct['cost_usd'] == 3
    monkeypatch.setattr(usage._hermes_usage, 'state_dbs', lambda: ['fixture'])
    monkeypatch.setattr(usage._hermes_usage, 'model_entries', lambda *a, **kw: [
        {'source': 'hermes', 'provider': 'omniroute', 'model': 'parent', 'totals': {'total': 10},
         'cost_usd': None, 'daily': []}])
    with_hermes = usage.build_model_breakdown(1)
    assert with_hermes['sources']['hermes']['estimated_cost_usd'] == 0
    assert with_hermes['sources']['hermes']['unpriced_models'] == 0
    monkeypatch.setattr(usage._hermes_usage, 'state_dbs', lambda: [])
    # Router outage/retention must not make unrelated overview partial.
    monkeypatch.setattr(om, 'collect', lambda *a, **kw: {'models': [], 'daily': [], 'status': 'unavailable', 'source': {'complete': False}})
    failed = usage.build_model_breakdown(1)
    assert failed['totals'] == result['totals']
    assert failed['sources']['openclaw'] == result['sources']['openclaw']
    # A router-only request neither collects agents nor calls the account analytics.
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *a: pytest.fail('unexpected Gateway read'))
    monkeypatch.setattr(nr.Client, 'get', lambda *a, **kw: pytest.fail('unexpected account analytics'))
    usage.build_model_breakdown(7, source='omniroute')
