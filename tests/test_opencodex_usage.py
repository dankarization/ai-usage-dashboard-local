"""Offline synthetic fixtures only: no Mac, LAN, admin token or live database."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import pytest

import opencodex_collect as collector
import opencodex_usage as usage

FIXTURE = Path(__file__).parent / 'fixtures/opencodex-usage-v1.json'


@pytest.fixture
def export():
    return json.loads(FIXTURE.read_text())


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    monkeypatch.setenv('OPENCODEX_USAGE_SOURCE_ID', 'fixture-mac')
    path = tmp_path / 'private' / 'opencodex.sqlite'
    monkeypatch.setenv('OPENCODEX_USAGE_DB', str(path))
    return path


def body(export):
    return export['windows'][0]['response']


def test_pinned_custom_utc_contract_preserves_provider_model_and_unknown_account(export):
    identity, rows = usage.normalize_export(export)
    assert identity == 'fixture-mac'
    assert export['upstream_sha'] == usage.UPSTREAM_SHA
    assert body(export)['until'] - body(export)['since'] == 86400000 - 1
    assert rows[0]['upstream_sha'] == usage.UPSTREAM_SHA
    assert rows[0]['total_tokens'] == 300
    assert [(m['provider'], m['model']) for m in rows[0]['models']] == [
        ('openai', 'gpt-5'), ('private-route', 'alias/gpt-5'), ('private-route', 'gpt-5')]
    assert all(m['account'] is None for m in rows[0]['models'])
    assert rows[0]['models'][1]['unresolved_requested_model'] is True
    assert rows[0]['accounts'] == []
    assert 'estimatedCostUsd' not in json.dumps(rows)


def test_repeated_import_is_idempotent_and_no_billing_or_double_count(export, cache):
    first = usage.import_snapshot(export)
    saved = cache.read_bytes()
    assert first['days_changed'] == 1
    assert usage.import_snapshot(export)['days_changed'] == 0
    assert cache.read_bytes() == saved
    with sqlite3.connect(cache) as conn:
        assert conn.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 1
    view = usage.view(1)
    assert view['status'] == 'saved' and view['additive'] is False
    assert sum(m['total_tokens'] for m in view['models']) == 300  # not plus accounts!
    assert view['billed_cost_usd'] is view['balance_usd'] is None
    assert view['estimated_cost_usd'] == .001
    assert cache.stat().st_mode & 0o777 == 0o600
    assert cache.parent.stat().st_mode & 0o777 == 0o700


def test_newer_snapshot_replaces_not_adds_and_old_source_cannot_overwrite(export, cache):
    usage.import_snapshot(export)
    newer = deepcopy(export)
    body(newer)['generatedAt'] += 1
    body(newer)['models'][0]['totalTokens'] += 10
    body(newer)['summary']['totalTokens'] += 10
    usage.import_snapshot(newer)
    assert usage.view(1)['daily'][0]['total_tokens'] == 310
    old = cache.read_bytes()
    with pytest.raises(usage.InvalidSnapshot):
        usage.import_snapshot(export)
    assert cache.read_bytes() == old


@pytest.mark.parametrize('mutation', [
    lambda b: b.update(usageIncomplete=True),
    lambda b: b.update(historyTruncated=True),
    lambda b: b.update(entriesTruncated=True),
    lambda b: b.update(entriesDropped=1),
    lambda b: b.update(truncatedPrefixBytes=1),
    lambda b: b.update(error='read_failed'),
    lambda b: b.update(warnings=['partial']),
    lambda b: b.pop('historyTruncated'),
    lambda b: b.update(until=b['until'] + 1),
    lambda b: b.update(since=True),
    lambda b: b.update(surface='codex'),
    lambda b: b.update(sourceFilter={'client':'codex'}),
    lambda b: b.update(filter={'provider':'openai'}),
    lambda b: b['summary'].update(unreportedRequests=1),
    lambda b: b['summary'].update(unsupportedRequests=1),
    lambda b: b['summary'].update(estimatedRequests=1),
    lambda b: b['summary'].update(reportedRequests=2),
    lambda b: b['summary'].update(totalTokens=301),
    lambda b: b['models'][0].update(provider='other', model='other'),
    lambda b: b['models'].append(b['models'][0]),
    lambda b: b['models'][0].update(totalTokens=-1),
    lambda b: b['models'][0].update(totalTokens=3.5),
    lambda b: b['models'][0].update(inputTokens=float('nan')),
    lambda b: b['models'][0].update(model='bad\nlabel'),
    lambda b: b['models'][0].pop('outputTokens'),
    lambda b: b.update(generatedAt=2**53 - 1),
])
def test_partial_invalid_or_collapsed_responses_preserve_last_snapshot(export, cache, mutation):
    usage.import_snapshot(export)
    saved = cache.read_bytes()
    invalid = deepcopy(export)
    mutation(body(invalid))
    with pytest.raises((ValueError, TypeError)):
        usage.import_snapshot(invalid)
    assert cache.read_bytes() == saved
    assert usage.view(1)['daily'][0]['total_tokens'] == 300


def test_batch_validation_and_transaction_conflict_are_atomic(export, cache):
    usage.import_snapshot(export)
    saved = cache.read_bytes()
    batch = deepcopy(export)
    batch['start_date'] = '2026-11-22'
    previous = deepcopy(batch['windows'][0])
    previous['date'] = '2026-11-22'
    previous['response']['since'], previous['response']['until'] = usage.bounds(previous['date'])
    batch['windows'].insert(0, previous)
    body(batch)['generatedAt'] += 10
    # Last row conflicts with an existing generation. No earlier row may survive.
    batch['windows'][1]['response']['models'][0]['inputTokens'] += 1
    with pytest.raises(usage.InvalidSnapshot):
        usage.import_snapshot(batch)
    assert cache.read_bytes() == saved
    batch['windows'].pop()
    with pytest.raises(usage.InvalidSnapshot):
        usage.import_snapshot(batch)
    assert cache.read_bytes() == saved


def test_private_fields_are_not_persisted_and_cache_read_is_not_added_twice(export, cache):
    body(export).update(logs=['DO_NOT_STORE'], prompt='DO_NOT_STORE', token='DO_NOT_STORE')
    body(export)['models'][0]['headers'] = {'Authorization': 'DO_NOT_STORE'}
    usage.import_snapshot(export)
    assert b'DO_NOT_STORE' not in cache.read_bytes()
    view = usage.view(1)
    assert view['daily'][0]['models'][0]['cache_read_tokens'] == 10
    assert view['daily'][0]['total_tokens'] == 300
    assert view['models'][0]['cache_read_tokens'] == 10  # sum reported categories only


def test_missing_days_unknown_and_offline_does_not_age_out_saved_data(export, cache):
    usage.import_snapshot(export)
    view = usage.view(7)
    assert view['end_date'] == '2026-11-23'
    assert view['coverage_complete'] is False
    assert view['daily'][0]['total_tokens'] is None
    assert view['daily'][-1]['total_tokens'] == 300
    assert 'stale' not in json.dumps(view).lower()


def test_no_cross_device_merging_or_wrong_source_admission(export, cache):
    with pytest.raises(usage.InvalidSnapshot):
        usage.import_snapshot(export, expected_source='another-mac')
    assert not cache.exists()
    usage.import_snapshot(export)
    assert usage.view(1, identity='another-mac')['status'] == 'unavailable'


def test_disabled_reads_and_collection_are_opt_in_and_never_create_state(export, cache, monkeypatch):
    monkeypatch.delenv('OPENCODEX_USAGE_ENABLED')
    assert usage.view(1)['status'] == 'disabled'
    with pytest.raises(usage.InvalidSnapshot):
        collector.collect(input_path=FIXTURE)
    assert not cache.exists()


@pytest.mark.parametrize('kind', ['foreign', 'newer', 'corrupt', 'symlink'])
def test_foreign_and_unsafe_database_refused_untouched(export, cache, kind):
    cache.parent.mkdir()
    if kind == 'corrupt':
        cache.write_bytes(b'not sqlite')
    elif kind == 'symlink':
        other = cache.with_name('target')
        other.write_bytes(b'untouched')
        cache.symlink_to(other)
    else:
        with sqlite3.connect(cache) as conn:
            if kind == 'foreign':
                conn.execute('CREATE TABLE foreign_table (id)')
            else:
                conn.execute(f'PRAGMA application_id={usage.APP_ID}')
                conn.execute('PRAGMA user_version=99')
    saved = cache.read_bytes()
    with pytest.raises((ValueError, sqlite3.Error)):
        usage.import_snapshot(export)
    assert cache.read_bytes() == saved


def test_http_failure_does_not_even_open_cache(export, cache, monkeypatch):
    usage.import_snapshot(export)
    saved = cache.read_bytes()
    def offline(*args, **kwargs):
        raise TimeoutError('offline')
    monkeypatch.setattr(collector, 'fetch_export', offline)
    with pytest.raises(TimeoutError):
        collector.collect(base_url='http://127.0.0.1:9999', days=1, end_date='2026-11-23')
    assert cache.read_bytes() == saved


def test_cli_offline_import_and_safe_error_output(export, cache, capsys):
    assert collector.main(['--input', str(FIXTURE)]) == 0
    assert 'days_changed' in capsys.readouterr().out
    assert collector.main(['--input', str(FIXTURE)]) == 0
    assert '"days_changed": 0' in capsys.readouterr().out
    assert collector.main(['--base-url', 'http://secret:secret@example.invalid:1234']) == 1
    output = capsys.readouterr()
    assert 'secret' not in output.err
    assert 'unchanged' in output.err


def test_optional_token_categories_stay_unknown_not_zero(export):
    body(export)['models'][0].pop('cacheReadInputTokens')
    rows = usage.normalize_export(export)[1]
    assert rows[0]['models'][0]['cache_read_tokens'] is None
