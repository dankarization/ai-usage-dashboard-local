"""Gateway aggregation boundaries and saved-window fallback for dashboard history."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import subprocess
import sys
import textwrap

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard_store
import openclaw_usage
from auto_usage import build_model_breakdown


REPO = Path(__file__).resolve().parent.parent
START, END = '2026-10-01', '2026-10-07'
AGGREGATE = {
    'byModel': [
        {'provider': 'xai', 'model': 'grok-4.7',
         'totals': {'input': 5, 'output': 3, 'cacheRead': 2, 'cacheWrite': 0,
                    'totalTokens': 10, 'totalCost': 0.2}},
        {'provider': 'nordrouter', 'model': 'nr-model',
         'totals': {'totalTokens': 21, 'totalCost': 0.5}},
    ],
    'modelDaily': [
        {'date': '2026-10-07', 'provider': 'xai', 'model': 'grok-4.7',
         'tokens': 10, 'cost': 0.2},
        {'date': '2026-10-07', 'provider': 'nordrouter', 'model': 'nr-model',
         'tokens': 21, 'cost': 0.5},
    ],
}

READER_SOURCE = textwrap.dedent('''
    import json, sys
    sys.path.insert(0, sys.argv[1])
    import openclaw_usage

    def failing_read(start, end):
        raise TimeoutError('Gateway timed out')

    result = openclaw_usage.load_with_cache(7, '2026-10-01', '2026-10-07',
                                            fetch=failing_read)
    print(json.dumps({key: result[key] for key in
                      ('status', 'cached_at_utc', 'error', 'response')}))
''')


def live_read(start_date, end_date):
    """A successful Gateway read; the aggregate is copied so tests cannot alias it."""
    aggregates = json.loads(json.dumps(AGGREGATE))
    for row in aggregates['modelDaily']:
        row['date'] = end_date
    return {'aggregates': aggregates, 'cacheStatus': {'status': 'fresh'}}


def failing_read(*_args):
    raise RuntimeError('gateway down')


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Point the dashboard-owned stored windows at a disposable directory."""
    root = tmp_path / 'openclaw'
    monkeypatch.setenv('OPENCLAW_USAGE_CACHE_DIR', str(root))
    monkeypatch.setenv('AI_USAGE_DASHBOARD_DB', str(root / 'dashboard.sqlite'))
    return root


def snapshot_row(days=7, start=START, end=END):
    """The saved row for one window, read straight from the dashboard database."""
    return dashboard_store.read_snapshot(
        openclaw_usage.db_file(),
        dashboard_store.window_key(days, start, end))


def saved_window(days=7, start=START, end=END):
    """The stored window as the module sees it: original time plus payload."""
    row = snapshot_row(days, start, end)
    assert row is not None
    return {'window': {'days': row['days'], 'start_date': row['start_date'],
                       'end_date': row['end_date']},
            'fetched_at_utc': row['fetched_at_utc'],
            'data': json.loads(row['payload'])}


def stored_rows():
    """Every stored window key, newest first, for retention assertions."""
    with dashboard_store.connection(openclaw_usage.db_file(), create=False) as conn:
        return [row[0] for row in conn.execute(
            'SELECT window_key FROM openclaw_usage_snapshot'
            ' ORDER BY fetched_at_utc DESC, rowid DESC')]


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


@pytest.mark.parametrize('total_cost,missing,expected_cost,expected_kind', [
    (70.6175, 326, 70.6175, 'partial'),
    (0, 7, None, None),
    (-1, 7, None, None),
    (0.1262, 0, 0.1262, 'reported'),
    (0, 0, 0, 'reported'),
    (None, 0, None, None),
    ('0.2', 0, None, None),
])
def test_openclaw_modeled_cost_completeness(total_cost, missing, expected_cost, expected_kind):
    response = {'aggregates': {'byModel': [{
        'provider': 'openai', 'model': 'gpt-6.1-sol',
        'totals': {'totalTokens': 100, 'totalCost': total_cost,
                   'missingCostEntries': missing},
    }]}}
    row, = openclaw_usage.model_entries(response, include_daily=False)
    assert row['cost_usd'] == expected_cost
    assert row['cost_kind'] == expected_kind


def test_breakdown_reprices_all_categories_despite_partial_native_cost(monkeypatch):
    response = {'aggregates': {'byModel': [
        {'provider': 'openai', 'model': 'gpt-6.1-sol', 'totals': {
            'input': 90, 'output': 10, 'cacheRead': 0, 'cacheWrite': 0,
            'totalTokens': 100, 'totalCost': 70.6175, 'missingCostEntries': 326}},
        {'provider': 'openai', 'model': 'gpt-5.6-terra', 'totals': {
            'input': 15, 'output': 5, 'cacheRead': 0, 'cacheWrite': 0,
            'totalTokens': 20, 'totalCost': 0.1262, 'missingCostEntries': 0}},
        {'provider': 'custom', 'model': 'unpriced-test', 'totals': {
            'totalTokens': 10, 'totalCost': 0, 'missingCostEntries': 2}},
        {'provider': 'nordrouter', 'model': 'nr-model', 'totals': {
            'totalTokens': 50, 'totalCost': 0, 'missingCostEntries': 7}},
    ]}}
    monkeypatch.setattr(openclaw_usage, 'load_with_cache', lambda *_: {
        'response': response, 'status': 'ok', 'cached_at_utc': None,
        'error': None, 'gateway_cache_status': 'fresh',
    })
    result = build_model_breakdown(days=7, source='openclaw')
    rows = {row['model']: row for row in result['models']}
    assert rows['gpt-6.1-sol']['cost_usd'] == pytest.approx(0.00028)
    assert rows['gpt-6.1-sol']['cost_kind'] == 'estimated'
    assert rows['nr-model']['cost_usd'] is None
    assert rows['nr-model']['cost_kind'] is None
    source = result['sources']['openclaw']
    assert source['estimated_cost_usd'] == pytest.approx(0.00028 + 0.00009)
    assert source['priced_models'] == 2
    assert source['unpriced_models'] == 1


def test_breakdown_uses_gateway_and_direct_nordrouter_only(offline_legacy_loader, monkeypatch):
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *_: {'aggregates': {
        'byModel': [{'provider': 'xai', 'model': 'grok-4.7',
                     'totals': {'input': 5, 'output': 3, 'cacheRead': 2,
                                'cacheWrite': 0, 'totalTokens': 10, 'totalCost': 0.2}},
                    {'provider': 'local', 'model': 'local-test',
                     'totals': {'totalTokens': 2}},
                    {'provider': 'nordrouter', 'model': 'nr-model',
                     'totals': {'totalTokens': 21, 'totalCost': 0.5}}],
        'modelDaily': [{'date': '2026-10-07', 'provider': 'xai',
                        'model': 'grok-4.7', 'tokens': 10, 'cost': 0.2},
                       {'date': '2026-10-07', 'provider': 'nordrouter',
                        'model': 'nr-model', 'tokens': 21, 'cost': 0.5},
                       {'date': '2026-10-07', 'provider': 'local',
                        'model': 'local-test', 'tokens': 2}],
    }, 'cacheStatus': {'status': 'fresh'}})
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
    monkeypatch.setattr('auto_usage.get_model_date_range', lambda days: ('2026-10-01', '2026-10-07', None, None))
    monkeypatch.setattr('auto_usage._nordrouter_usage.Client', DirectClient)
    # The real Hermes databases on this host are not part of this contract.
    monkeypatch.setattr('auto_usage._hermes_usage.state_dbs', lambda *_: [])
    result = build_model_breakdown(days=7)
    assert [(r['source'], r['provider'], r['totals']['total']) for r in result['models']] == [
        ('openclaw', 'nordrouter', 21), ('nordrouter', 'nordrouter', 20),
        ('openclaw', 'xai', 10), ('openclaw', 'local', 2)]
    assert all('account' not in row for row in result['models'])
    assert result['source_daily']['nordrouter'][0]['tokens'] == 20
    assert result['totals']['total'] == 32
    assert result['sources']['openclaw']['estimated_cost_usd'] == pytest.approx(0.000029)
    assert result['sources']['openclaw']['unpriced_models'] == 0
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


# --- saved-window fallback -------------------------------------------------


def test_successful_live_read_is_saved_aggregate_only_and_replayed(cache):
    """The window of a successful read is retained, and only after a failure used."""
    live = openclaw_usage.load_with_cache(7, START, END, fetch=live_read)
    assert live['status'] == 'ok'
    assert live['response']['aggregates']['byModel']
    # A live read is current data, so it carries no saved-fetch time.
    assert live['cached_at_utc'] is None
    assert live['error'] is None

    record = saved_window()
    assert record['window'] == {'days': 7, 'start_date': START, 'end_date': END}
    assert datetime.fromisoformat(record['fetched_at_utc']).tzinfo is not None
    # Only the projected aggregate survives; no other Gateway field is stored.
    assert set(record['data']) == {'aggregates', 'cacheStatus'}
    assert record['data']['cacheStatus']['status'] == 'fresh'
    assert set(record['data']['aggregates']) == {'byModel', 'modelDaily'}
    assert openclaw_usage.db_file().exists()

    replay = openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)
    assert replay['status'] == 'cached'
    assert replay['response'] == record['data']
    assert replay['cached_at_utc'] == record['fetched_at_utc']
    assert replay['error'] == 'RuntimeError: gateway down'


def test_saved_window_survives_a_fresh_process(cache, tmp_path):
    """A service restart must not lose the window: a new process reads the same row."""
    openclaw_usage.load_with_cache(7, START, END, fetch=live_read)
    record = saved_window()

    script = tmp_path / 'read_saved_window.py'
    script.write_text(READER_SOURCE)
    result = subprocess.run([sys.executable, str(script), str(REPO)],
                            capture_output=True, text=True, timeout=60,
                            env={**os.environ})
    assert result.returncode == 0, result.stderr
    replay = json.loads(result.stdout)
    assert replay['status'] == 'cached'
    assert replay['cached_at_utc'] == record['fetched_at_utc']
    assert replay['response'] == record['data']
    assert 'TimeoutError' in replay['error']


def test_live_timeout_serves_the_saved_window(cache):
    """A bounded wait ends in the saved window, never in a fabricated zero."""
    openclaw_usage.load_with_cache(7, START, END, fetch=live_read)

    def slow_read(start_date, end_date):
        raise subprocess.TimeoutExpired('openclaw gateway call',
                                        30)

    loaded = openclaw_usage.load_with_cache(7, START, END, fetch=slow_read)
    assert loaded['status'] == 'cached'
    assert loaded['response']['aggregates']['byModel']
    # The banner names the timeout and its bound instead of a truncated command line.
    assert loaded['error'] == 'TimeoutExpired: gateway command exceeded 30s'


def test_unrecognized_response_shapes_fall_back_instead_of_raising(cache):
    """An unknown Gateway reply is handled like a failure, never as a crash."""
    openclaw_usage.load_with_cache(7, START, END, fetch=live_read)

    def odd_read(start_date, end_date):
        return {'aggregates': AGGREGATE, 'cacheStatus': 'refreshing'}

    loaded = openclaw_usage.load_with_cache(7, START, END, fetch=odd_read)
    assert loaded['status'] == 'cached'
    assert loaded['upstream_status'] == 'unknown'
    assert loaded['snapshot_complete'] is True

    for bad in ({'aggregates': 'nope'}, {}, None, {'other': 1}):
        result = openclaw_usage.load_with_cache(7, START, END, fetch=lambda *_, b=bad: b)
        # An unusable live reply still falls back to the saved window.
        assert result['status'] == 'cached', bad
        assert result['error']

    # Malformed rows cannot erase the successful saved window.
    before = saved_window()
    malformed = openclaw_usage.load_with_cache(
        7, START, END, fetch=lambda *_: {'aggregates': {'byModel': ['nope'], 'modelDaily': 'x'}})
    assert malformed['status'] == 'cached'
    assert malformed['response'] == before['data']
    assert saved_window() == before
    empty = openclaw_usage.load_with_cache(
        7, START, END, fetch=lambda *_: {'aggregates': {'byModel': [], 'modelDaily': []}})
    assert empty['status'] == 'cached'  # a valid but unverified zero cannot erase a full window
    assert empty['response']['aggregates']['byModel']
    verified_zero = openclaw_usage.load_with_cache(7, START, END, fetch=lambda *_: {
        'aggregates': {'byModel': [], 'modelDaily': []}, 'cacheStatus': {'status': 'fresh'}})
    assert verified_zero['status'] == 'ok'
    assert verified_zero['response']['aggregates']['byModel'] == []


def test_repeated_failures_never_promote_or_rewrite_the_saved_window(cache):
    """A failing read must not advance the saved time or replace saved numbers."""
    original = datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc)
    openclaw_usage.write_snapshot(7, START, END, live_read(START, END), fetched_at=original)
    before = snapshot_row()
    assert before['fetched_at_utc'] == original.isoformat()

    first = openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)
    second = openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)
    assert first['cached_at_utc'] == second['cached_at_utc'] == original.isoformat()
    assert snapshot_row() == before

    # A later successful read is the only thing that moves the saved time.
    recovered = openclaw_usage.load_with_cache(7, START, END, fetch=live_read)
    assert recovered['status'] == 'ok'
    assert snapshot_row()['fetched_at_utc'] != original.isoformat()


def test_only_the_exact_window_is_reused(cache):
    """Yesterday's window must never answer today's, and periods stay separate."""
    openclaw_usage.load_with_cache(7, START, END, fetch=live_read)
    openclaw_usage.load_with_cache(1, '2026-10-07', '2026-10-07', fetch=live_read)

    saved = saved_window()
    cached = openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)
    assert cached['response'] == saved['data']
    assert cached['cached_at_utc'] == saved['fetched_at_utc']

    def status(days, start, end):
        loaded = openclaw_usage.load_with_cache(days, start, end, fetch=failing_read)
        if loaded['status'] == 'unavailable':
            assert loaded['response'] is None
            assert loaded['cached_at_utc'] is None
            assert loaded['error'] == 'RuntimeError: gateway down'
        return loaded['status']

    assert status(7, START, END) == 'cached'
    # The same period shifted by one day describes a different window.
    assert status(7, '2026-10-02', '2026-10-08') == 'unavailable'
    # A local-calendar-day window rolls over: yesterday cannot be shown as today.
    assert status(1, '2026-10-08', '2026-10-08') == 'unavailable'
    assert status(1, '2026-10-07', '2026-10-07') == 'cached'
    # 1d/7d/30d are distinct keys even over overlapping dates.
    assert status(30, START, END) == 'unavailable'
    assert status(1, START, END) == 'unavailable'


@pytest.mark.parametrize('has_older_window', [False, True])
def test_api_gateway_failure_requires_exact_saved_window(offline_legacy_loader, cache, monkeypatch, has_older_window):
    """A failed Gateway read returns unavailable, not a 500 or another window."""
    from fastapi.testclient import TestClient
    import local_display_service

    original = datetime(2026, 10, 7, 9, 30, tzinfo=timezone.utc)
    if has_older_window:
        openclaw_usage.write_snapshot(7, START, END, live_read(START, END),
                                      fetched_at=original)
    monkeypatch.setattr(local_display_service, 'load_env', lambda: None)
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', failing_read)
    monkeypatch.setattr('auto_usage.get_model_date_range',
                        lambda days: ('2026-10-02', '2026-10-08', None, None))
    client = TestClient(local_display_service.app, raise_server_exceptions=False)
    response = client.get('/api/v1/model-breakdown?days=7&source=openclaw')
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['meta']['openclaw_status'] == 'unavailable'
    assert body['meta']['openclaw_cached_at_utc'] is None
    assert body['meta']['openclaw_error'] == 'RuntimeError: gateway down'
    assert body['models'] == []
    assert body['sources']['openclaw']['complete'] is False

    if has_older_window:
        monkeypatch.setattr('auto_usage.get_model_date_range', lambda days: (START, END, None, None))
        response = client.get('/api/v1/model-breakdown?days=7&source=openclaw')
        assert response.status_code == 200, response.text
        assert response.json()['meta']['openclaw_status'] == 'cached'
        assert response.json()['meta']['openclaw_cached_at_utc'] == original.isoformat()


def test_retention_keeps_the_cache_bounded_and_ignores_foreign_files(cache):
    """Old windows are pruned from the table; unrelated tables and files survive."""
    cache.mkdir(parents=True)
    unrelated = cache / 'notes.txt'
    unrelated.write_text('keep me')
    foreign = cache / 'other.sqlite'
    foreign.write_bytes(b'not a dashboard database')

    for index in range(openclaw_usage.CACHE_WINDOW_LIMIT + 6):
        day = f'2026-09-{index + 1:02d}'
        openclaw_usage.write_snapshot(1, day, day, live_read(day, day))

    with dashboard_store.connection(openclaw_usage.db_file(), create=False) as conn:
        kept = conn.execute(
            'SELECT COUNT(*) FROM openclaw_usage_snapshot').fetchone()[0]
    assert kept <= openclaw_usage.CACHE_WINDOW_LIMIT
    assert unrelated.read_text() == 'keep me'
    assert foreign.read_bytes() == b'not a dashboard database'


def test_unusable_or_unknown_cache_entries_are_ignored(cache, tmp_path):
    """Corrupt, mismatched, foreign, or malformed saved data degrades to unavailable."""
    target = openclaw_usage.db_file()
    cache.mkdir(parents=True)

    target.write_bytes(b'{not a database')
    assert openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)['status'] == 'unavailable'
    assert target.read_bytes() == b'{not a database'
    with pytest.raises(dashboard_store.StoreUnavailable):
        with dashboard_store.connection(target, create=False):
            pass

    # Someone else's SQLite file is refused, not repaired or overwritten.
    target.unlink()
    foreign = sqlite3.connect(target)
    foreign.execute('CREATE TABLE someone_elses (id INTEGER PRIMARY KEY)')
    foreign.execute('INSERT INTO someone_elses (id) VALUES (1)')
    foreign.commit()
    foreign.close()
    before = target.read_bytes()
    assert openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)['status'] == 'unavailable'
    assert target.read_bytes() == before

    # A database written by a newer dashboard version is refused.
    target.unlink()
    with dashboard_store.connection(target, create=True) as conn:
        conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    assert openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)['status'] == 'unavailable'

    # A mismatched or malformed row is not a usable window either.
    target.unlink()
    good = {'days': 7, 'start_date': START, 'end_date': END,
            'fetched_at_utc': '2026-10-07T09:30:00+00:00',
            'payload': json.dumps({'aggregates': AGGREGATE})}
    for broken in (
        {**good, 'start_date': '2026-09-30'},
        {**good, 'days': 30},
        # A naive timestamp cannot be placed on the timeline, so it is not used.
        {**good, 'fetched_at_utc': '2026-10-07T09:30:00'},
        {**good, 'payload': json.dumps({'aggregates': None})},
        {**good, 'payload': 'aggregates'},
        {**good, 'payload': '{not json'},
    ):
        target.unlink(missing_ok=True)
        with dashboard_store.connection(target, create=True) as conn:
            conn.execute('INSERT INTO openclaw_usage_snapshot (window_key, days,'
                         ' start_date, end_date, fetched_at_utc, payload)'
                         ' VALUES (?, ?, ?, ?, ?, ?)',
                         (dashboard_store.window_key(broken['days'], broken['start_date'],
                                                     broken['end_date']),
                          broken['days'], broken['start_date'], broken['end_date'],
                          broken['fetched_at_utc'], broken['payload']))
        assert openclaw_usage.load_with_cache(
            7, START, END, fetch=failing_read)['status'] == 'unavailable', broken

    # An unrecognized period never becomes a lookup key, and a symlinked
    # database is refused rather than followed.
    assert openclaw_usage.read_snapshot(0, START, END) is None
    assert openclaw_usage.read_snapshot(7, 'yesterday', END) is None
    with pytest.raises(ValueError):
        openclaw_usage.write_snapshot(7, 'yesterday', END, live_read(START, END))
    link = tmp_path / 'linked.sqlite'
    link.symlink_to(target)
    assert openclaw_usage.read_snapshot(7, START, END, cache_dir=link) is None
    with pytest.raises(ValueError):
        openclaw_usage.write_snapshot(7, START, END, live_read(START, END), cache_dir=link)


def test_failure_text_is_bounded_and_free_of_paths_and_secrets(cache):
    """The banner text is a summary, never a credential or a private path."""
    openclaw_usage.load_with_cache(7, START, END, fetch=live_read)
    secret = 'Bearer sk-' + 'a' * 30

    def leaky_read(start_date, end_date):
        raise RuntimeError(f'{secret} at {Path.home()}/private/thing\nsecond line')

    loaded = openclaw_usage.load_with_cache(7, START, END, fetch=leaky_read)
    text = loaded['error']
    assert loaded['status'] == 'cached'
    assert 'sk-' not in text
    assert str(Path.home()) not in text
    assert '\n' not in text
    assert len(text) <= 200

    # A real Gateway CLI failure reports its reason, not its whole command line.
    def cli_failure(start_date, end_date):
        raise subprocess.CalledProcessError(1, ['openclaw', 'gateway', 'call'])

    assert openclaw_usage.load_with_cache(7, START, END, fetch=cli_failure)['error'] == \
        'CalledProcessError: gateway command failed with exit status 1'


def test_breakdown_serves_the_saved_window_with_time_and_failure_text(offline_legacy_loader, cache, monkeypatch):
    """The API reports a saved window as stale, with its original fetch time."""
    monkeypatch.setattr('auto_usage.get_model_date_range', lambda days: (START, END, None, None))
    monkeypatch.setattr('auto_usage._hermes_usage.state_dbs', lambda *_: [])
    monkeypatch.delenv('NORDROUTER_API_KEY', raising=False)
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', live_read)
    live = build_model_breakdown(days=7, source='openclaw')
    assert live['meta']['openclaw_status'] == 'ok'
    assert live['meta']['openclaw_cached_at_utc'] is None
    saved = saved_window()

    monkeypatch.setattr(openclaw_usage, 'fetch_usage', failing_read)
    cached = build_model_breakdown(days=7, source='openclaw')
    assert cached['meta']['openclaw_status'] == 'cached'
    assert cached['meta']['openclaw_cached_at_utc'] == saved['fetched_at_utc']
    assert cached['meta']['openclaw_error'] == 'RuntimeError: gateway down'
    assert cached['sources']['openclaw']['complete'] is False
    # Never a fabricated zero or a total that claims to be current.
    assert cached['sources']['openclaw']['tokens'] == 10
    assert cached['totals']['total'] is None
    # A missing window still reports an honest unavailable.
    monkeypatch.setattr('auto_usage.get_model_date_range', lambda days: ('2026-01-01', '2026-01-07', None, None))
    assert build_model_breakdown(days=7, source='openclaw')['meta']['openclaw_status'] == 'unavailable'


def test_cached_and_live_breakdowns_agree_except_for_staleness(offline_legacy_loader, cache, monkeypatch):
    """Identical input must produce identical accounting, stale status aside."""
    monkeypatch.setattr('auto_usage.get_model_date_range', lambda days: (START, END, None, None))
    monkeypatch.setattr('auto_usage._hermes_usage.state_dbs', lambda *_: [])
    monkeypatch.delenv('NORDROUTER_API_KEY', raising=False)
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', live_read)
    live = build_model_breakdown(days=7, source='openclaw')

    monkeypatch.setattr(openclaw_usage, 'fetch_usage', failing_read)
    cached = build_model_breakdown(days=7, source='openclaw')

    assert cached['models'] == live['models']
    assert cached['source_daily'] == live['source_daily']
    assert cached['totals'] == live['totals']
    for key in ('tokens', 'estimated_cost_usd', 'priced_models', 'unpriced_models'):
        assert cached['sources']['openclaw'][key] == live['sources']['openclaw'][key]
    assert (cached['sources']['openclaw_nordrouter_comparison_tokens']
            == live['sources']['openclaw_nordrouter_comparison_tokens'] == 21)
    assert live['sources']['openclaw']['complete'] is True
    assert cached['sources']['openclaw']['complete'] is False
    # Differences are confined to the documented staleness fields and to the
    # generation timestamps of the two separate calls.
    changed = {key for key in live['meta'] if live['meta'][key] != cached['meta'][key]}
    assert changed <= {'generated_at', 'generated_at_utc', 'openclaw_status',
                       'openclaw_cached_at_utc', 'openclaw_error', 'openclaw_upstream_status', 'openclaw_source_at_utc'}
    assert {'openclaw_status', 'openclaw_cached_at_utc', 'openclaw_error'} <= changed


# --- dashboard-owned database ---------------------------------------------


def test_database_is_created_owned_and_versioned(cache):
    """One additive, versioned schema in a file this dashboard owns."""
    target = openclaw_usage.db_file()
    assert not target.exists(), 'a read must not create runtime state'
    assert openclaw_usage.read_snapshot(7, START, END) is None
    assert not target.exists()

    openclaw_usage.write_snapshot(7, START, END, live_read(START, END))
    with dashboard_store.connection(target, create=False) as conn:
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        meta = dict(conn.execute('SELECT key, value FROM schema_meta'))
        columns = [row[1] for row in conn.execute(
            'PRAGMA table_info(openclaw_usage_snapshot)')]
        journal = conn.execute('PRAGMA journal_mode').fetchone()[0]
    assert {'schema_meta', 'openclaw_usage_snapshot'} <= tables
    assert meta['app'] == dashboard_store.APP_ID
    assert int(meta['schema_version']) == dashboard_store.SCHEMA_VERSION
    assert columns == ['window_key', 'days', 'start_date', 'end_date',
                       'fetched_at_utc', 'payload']
    assert journal.lower() == 'wal'
    assert target.stat().st_mode & 0o077 == 0, 'the database is private to the dashboard'


def test_initialization_is_idempotent_across_starts(cache):
    """A second start, and a second process, must not disturb stored windows."""
    openclaw_usage.write_snapshot(7, START, END, live_read(START, END))
    before = snapshot_row()
    for _ in range(3):
        with dashboard_store.connection(openclaw_usage.db_file(), create=True) as conn:
            conn.execute('SELECT 1').fetchone()
    assert snapshot_row() == before

    script = cache.parent / 'reopen.py'
    script.write_text(
        'import sys\n'
        'sys.path.insert(0, sys.argv[1])\n'
        'import dashboard_store, openclaw_usage\n'
        'with dashboard_store.connection(openclaw_usage.db_file(), create=True) as c:\n'
        '    print(c.execute("SELECT COUNT(*) FROM openclaw_usage_snapshot").fetchone()[0])\n')
    result = subprocess.run([sys.executable, str(script), str(REPO)],
                            capture_output=True, text=True, timeout=60,
                            env={**os.environ})
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == '1'
    assert snapshot_row() == before


def test_concurrent_writers_keep_every_window_intact(cache):
    """Two processes writing at once must not corrupt a window or the file."""
    script = cache.parent / 'writer.py'
    script.write_text(
        'import sys; sys.path.insert(0, sys.argv[1])\n'
        'import openclaw_usage\n'
        'start, end = sys.argv[2], sys.argv[3]\n'
        'openclaw_usage.write_snapshot(1, start, end,\n'
        '    {"aggregates": {"byModel": [{"provider": "xai", "model": "m",\n'
        '    "totals": {"totalTokens": 7, "totalCost": 0.1}}], "modelDaily": []}})\n'
        'print("done")\n')
    days = [f'2026-09-{index + 1:02d}' for index in range(8)]
    processes = [subprocess.Popen(
        [sys.executable, str(script), str(REPO), day, day],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={**os.environ}) for day in days]
    for process in processes:
        out, err = process.communicate(timeout=120)
        assert process.returncode == 0, err
        assert 'done' in out

    keys = [dashboard_store.window_key(1, day, day) for day in days]
    with dashboard_store.connection(openclaw_usage.db_file(), create=False) as conn:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        stored = dict(conn.execute(
            'SELECT window_key, payload FROM openclaw_usage_snapshot'))
    assert integrity == 'ok'
    # Every writer past the window limit may be pruned, but nothing is corrupt:
    # each surviving row still parses as the aggregate that wrote it.
    assert 0 < len(stored) <= openclaw_usage.CACHE_WINDOW_LIMIT
    for key, payload in stored.items():
        assert key in keys
        assert json.loads(payload)['aggregates']['byModel'][0]['totals']['totalTokens'] == 7


def test_legacy_json_snapshots_are_imported_once_and_left_in_place(cache):
    """Snapshots written before the database existed keep working, unmodified."""
    cache.mkdir(parents=True)
    legacy = cache / f'snapshot-days_7_start_{START}_end_{END}.json'
    record = {'window': {'days': 7, 'start_date': START, 'end_date': END},
              'fetched_at_utc': '2026-10-07T09:30:00+00:00',
              'data': {'aggregates': AGGREGATE}}
    legacy.write_text(json.dumps(record))
    original = legacy.read_bytes()

    loaded = openclaw_usage.read_snapshot(7, START, END)
    assert loaded is not None
    assert loaded['fetched_at_utc'] == '2026-10-07T09:30:00+00:00'
    assert loaded['data'] == record['data']
    assert legacy.read_bytes() == original, 'a legacy file is read, never moved or rewritten'

    # It is imported once: a later failing live read serves it as a saved window.
    replay = openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)
    assert replay['status'] == 'cached'
    assert replay['cached_at_utc'] == '2026-10-07T09:30:00+00:00'

    # An unrelated or malformed legacy file never becomes a usable window, and
    # a directory of junk is not turned into a database by a mere read.
    stray = cache / 'snapshot-days_7_start_2026-01-01_end_2026-01-07.json'
    stray.write_text('{not json')
    other = cache.parent / 'junk-only'
    (other / 'openclaw').mkdir(parents=True)
    (other / 'openclaw' / 'notes.txt').write_text('nothing here')
    assert openclaw_usage.read_snapshot(7, '2026-01-01', '2026-01-07') is None
    assert openclaw_usage.read_snapshot(
        7, '2026-01-01', '2026-01-07', cache_dir=other / 'openclaw') is None
    assert not (other / 'openclaw' / 'dashboard.sqlite').exists()
    assert stray.read_text() == '{not json'


def test_a_foreign_or_broken_database_is_never_repaired(cache):
    """Another program's file is refused untouched, and the page still answers."""
    target = openclaw_usage.db_file()
    cache.mkdir(parents=True)

    # SQLite, but not created by this dashboard.
    conn = sqlite3.connect(target)
    conn.execute('CREATE TABLE other_app (id INTEGER PRIMARY KEY, note TEXT)')
    conn.execute("INSERT INTO other_app (note) VALUES ('mine')")
    conn.commit()
    conn.close()
    before = target.read_bytes()
    assert openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)['status'] == 'unavailable'
    with pytest.raises(ValueError):
        openclaw_usage.write_snapshot(7, START, END, live_read(START, END))
    assert target.read_bytes() == before

    # Not SQLite at all.
    target.write_bytes(b'PK\x03\x04 not a database')
    before = target.read_bytes()
    assert openclaw_usage.read_snapshot(7, START, END) is None
    assert openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)['status'] == 'unavailable'
    assert target.read_bytes() == before

    # Written by a later dashboard schema than this code understands.
    target.unlink()
    with dashboard_store.connection(target, create=True) as conn:
        conn.execute("UPDATE schema_meta SET value = '99' WHERE key = 'schema_version'")
    before = target.read_bytes()
    assert openclaw_usage.load_with_cache(7, START, END, fetch=failing_read)['status'] == 'unavailable'
    assert target.read_bytes() == before


@pytest.mark.parametrize('status', ['partial', 'stale', 'refreshing'])
def test_partial_gateway_snapshot_never_completes_overview(offline_legacy_loader, cache, monkeypatch, status):
    monkeypatch.setattr('auto_usage.get_model_date_range', lambda days: (START, END, None, None))
    monkeypatch.setattr('auto_usage._hermes_usage.state_dbs', lambda: [])
    monkeypatch.delenv('NORDROUTER_API_KEY', raising=False)
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *a: {
        **live_read(START, END), 'cacheStatus': {'status': status}})
    result = build_model_breakdown(7, source='openclaw')
    assert result['meta']['openclaw_status'] == status
    assert result['sources']['openclaw']['complete'] is False
    assert result['sources']['openclaw']['tokens'] == 10
    assert result['totals']['total'] is None


@pytest.mark.parametrize('status', ['refreshing', 'partial', 'stale'])
def test_incomplete_zero_cannot_replace_full_models_or_original_time(cache, status):
    original = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    full = live_read(START, END)
    full.update(startDate=START, endDate=END, updatedAt=1791366000000)
    openclaw_usage.write_snapshot(7, START, END, full, fetched_at=original)
    before = snapshot_row()
    partial = {'aggregates': {'byModel': [], 'modelDaily': []},
               'startDate': START, 'endDate': END,
               'cacheStatus': {'status': status, 'pendingFiles': 2},
               'partial': {'reason': 'time-budget'}}
    for _ in range(2):
        result = openclaw_usage.load_with_cache(7, START, END, fetch=lambda *_: partial)
        assert result['status'] == 'cached'
        assert result['snapshot_complete'] is True
        assert result['upstream_status'] == status
        assert result['cached_at_utc'] == original.isoformat()
        assert result['source_at_utc'] == openclaw_usage._source_instant(full, None)
        assert result['response']['aggregates'] == full['aggregates']
        assert snapshot_row() == before


def test_budget_and_coverage_survive_sanitization_even_when_file_counts_are_zero(cache):
    response = {**live_read(START, END), 'startDate': START, 'endDate': END,
                'cacheStatus': {'status': 'fresh', 'pendingFiles': 0, 'staleFiles': 0},
                'partial': {'reason': 'time-budget', 'message': 'private diagnostic'},
                'coverage': {'strategy': 'all-agents-exact-empty-page-v1', 'complete': False,
                             'sessions': ['private'], 'requests': [{'status': 'partial', 'secret': 'never retain'}]},
                'sessions': ['private'], 'token': 'never retain'}
    result = openclaw_usage.load_with_cache(7, START, END, fetch=lambda *_: response)
    assert result['snapshot_complete'] is False
    assert result['gateway_cache_status'] == 'partial'
    stored = saved_window()['data']
    assert stored['partial'] == {'reason': 'time-budget'}
    assert stored['coverage']['complete'] is False
    assert 'private' not in json.dumps(stored)
    assert 'never retain' not in json.dumps(stored)


def test_legacy_or_partial_checkpoint_is_not_certified_and_only_full_can_replace_it(cache):
    unknown = {'aggregates': AGGREGATE}
    openclaw_usage.write_snapshot(7, START, END, unknown)
    before = snapshot_row()
    loaded = openclaw_usage.load_with_cache(7, START, END, fetch=lambda *_: {
        'aggregates': {'byModel': [], 'modelDaily': []}, 'cacheStatus': {'status': 'refreshing'}})
    assert loaded['status'] == 'cached' and not loaded['snapshot_complete']
    assert snapshot_row() == before
    recovered = openclaw_usage.load_with_cache(7, START, END, fetch=live_read)
    assert recovered['snapshot_complete'] and recovered['status'] == 'ok'
    assert snapshot_row() != before


@pytest.mark.parametrize('changed', [
    {'startDate': '2026-09-30'},
    {'endDate': '2026-10-06'},
    {'aggregates': {'byModel': AGGREGATE['byModel'], 'modelDaily': [
        {**AGGREGATE['modelDaily'][0], 'date': '2026-10-08'}]}},
])
def test_window_mismatch_cannot_admit_or_advance_snapshot(cache, changed):
    openclaw_usage.load_with_cache(7, START, END, fetch=live_read)
    before = snapshot_row()
    result = openclaw_usage.load_with_cache(7, START, END, fetch=lambda *_: {**live_read(START, END), **changed})
    assert result['status'] == 'cached'
    assert snapshot_row() == before


def test_fresh_label_with_missing_daily_rows_is_not_full(cache):
    reply = live_read(START, END)
    reply['aggregates']['modelDaily'] = []
    result = openclaw_usage.load_with_cache(7, START, END, fetch=lambda *_: reply)
    assert result['snapshot_complete'] is False
    assert result['gateway_cache_status'] == 'partial'


def test_first_partial_insert_cannot_race_over_a_complete_writer(cache, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    partial_ready, full_written = threading.Event(), threading.Event()
    store = dashboard_store.store_snapshot
    def reordered(*args, **kwargs):
        payload = json.loads(args[6])
        if payload['cacheStatus']['status'] == 'partial':
            partial_ready.set()
            assert full_written.wait(3)
        result = store(*args, **kwargs)
        if payload['cacheStatus']['status'] == 'fresh':
            full_written.set()
        return result
    monkeypatch.setattr(dashboard_store, 'store_snapshot', reordered)
    partial = {'aggregates': {'byModel': [], 'modelDaily': []}, 'cacheStatus': {'status': 'partial'}}
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(openclaw_usage.load_with_cache, 7, START, END, fetch=lambda *_: partial)
        assert partial_ready.wait(2)
        openclaw_usage.write_snapshot(7, START, END, live_read(START, END))
        settled = first.result(3)
    assert settled['status'] == 'cached' and settled['snapshot_complete']
    assert saved_window()['data']['aggregates'] == AGGREGATE
    assert saved_window()['data']['cacheStatus']['status'] == 'fresh'
