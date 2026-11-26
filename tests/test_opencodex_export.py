"""Synthetic Mac CLI shape and aggregate-only file contract, no actual Mac calls."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
import sqlite3
import time

import pytest

import opencodex_export as producer
import opencodex_usage as usage

FIXTURE = Path(__file__).parent / 'fixtures/opencodex-usage-v1.json'


def raw():
    return json.loads(FIXTURE.read_text())['windows'][0]['response']


def test_mac_allowlist_drops_all_account_and_arbitrary_metadata():
    data = raw()
    data['accounts'][0]['accountLogLabel'] = 'PRIVATE_ACCOUNT_SENTINEL'
    data.update(logs=['PRIVATE_LOG_SENTINEL'], config={'token':'PRIVATE_TOKEN_SENTINEL'})
    data['summary']['account'] = 'PRIVATE_ACCOUNT_SENTINEL'
    data['models'][0]['headers'] = {'auth':'PRIVATE_TOKEN_SENTINEL'}
    out = producer.envelope([dict(date='2026-11-23', response=usage.project_window(data))], 'fixture-mac')
    encoded = json.dumps(out)
    assert 'PRIVATE_' not in encoded and 'accounts' not in encoded and 'filter' not in encoded
    assert set(out['windows'][0]['response']) == usage.WINDOW_FIELDS
    assert out['schema'] == 2


def test_valid_98_percent_history_keeps_raw_counters_estimate_and_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    data = raw()
    data['summary'].update(requests=100, measuredRequests=98, reportedRequests=97,
                           estimatedRequests=1, unreportedRequests=2, coverageRatio=.98,
                           pricedRequests=96, unpricedRequests=2, unmeteredRequests=2,
                           estimatedCostUsd=12.3456, unresolvedSends=2)
    for row, requests, reported, estimated in zip(data['models'], [80, 10, 10], [77, 10, 10], [1, 0, 0]):
        row.update(requests=requests, reportedRequests=reported,
                   measuredRequests=reported+estimated, estimatedRequests=estimated)
    result = usage.normalize_day('2026-11-23', data)
    payload = producer.envelope([dict(date='2026-11-23',response=usage.project_window(data))], 'fixture-mac')
    path = tmp_path / 'own.sqlite'
    assert usage.import_snapshot(payload,path=path)['days_changed'] == 1
    assert usage.import_snapshot(payload,path=path)['days_changed'] == 0
    cached = usage.view(0,path=path,identity='fixture-mac')
    assert cached['measurement_coverage'] == .98 and cached['reported_counters']['unreportedRequests'] == 2
    assert cached['estimated_cost_usd'] == 12.3456 and cached['billed_cost_usd'] is None
    assert result['measurement_coverage'] == .98 and not result['measurement_complete']
    assert result['estimated_cost_usd'] == 12.3456
    assert result['reported_counters']['unreportedRequests'] == 2
    assert result['reported_counters']['unresolvedSends'] == 2
    assert result['total_tokens'] == 300 and result['accounts'] == []
    assert result['estimate_kind'] == 'source_reported_api_list_price_not_spend'


def test_missing_usage_is_not_zero_but_raw_zero_and_real_zero_are_preserved():
    data = raw()
    data['summary'].update(requests=1, reportedRequests=0, measuredRequests=0,
                           unreportedRequests=1, coverageRatio=0, totalTokens=0,
                           pricedRequests=0, estimatedCostUsd=0)
    data['models'] = [data['models'][0]]
    data['models'][0].update(requests=1, reportedRequests=0, measuredRequests=0,
                             totalTokens=0, inputTokens=0, outputTokens=0)
    result = usage.normalize_day('2026-11-23', data)
    assert result['total_tokens'] is None
    assert result['models'][0]['input_tokens'] is None
    assert result['models'][0]['reported_counters']['inputTokens'] == 0
    assert result['reported_estimated_cost_usd'] == 0 and result['estimated_cost_usd'] is None
    data['summary'].update(requests=0, unreportedRequests=0)
    data['models'] = []
    assert usage.normalize_day('2026-11-23', data)['total_tokens'] == 0


def test_bounded_backfill_beyond_90_days_no_pruning_and_real_duplicate_imports(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    data = raw()
    calls = []
    first = date(2026, 6, 1)
    end = first + timedelta(days=120)
    def reader(ocx, start=None, stop=None, *, deadline):
        calls.append((start, stop))
        body = deepcopy(data)
        if start is None:
            body.update(customWindow=False, snapshotWindowStart=usage.bounds(first.isoformat())[0],
                        snapshotWindowEnd=usage.bounds(end.isoformat())[1])
        else:
            body.update(since=start, until=stop)
        return body
    directory = tmp_path / 'private'
    receipt = producer.export_batches(directory, identity='fixture-mac', end_date=end.isoformat(),
                                     batch_days=30, max_batches=5, reader=reader)
    assert receipt == dict(batches_written=5, complete=True, next_start_date=None)
    assert len(calls) == 122  # One metadata discovery, then one exact UTC scan per day.
    path = tmp_path / 'db' / 'owned.sqlite'
    for f in sorted(directory.glob('*.json')):
        payload = usage.read_export(f)
        assert len(payload['windows']) <= 30
        usage.import_snapshot(payload, path=path)
        assert usage.import_snapshot(payload, path=path)['days_changed'] == 0
        assert f.stat().st_mode & 0o777 == 0o600
        assert 'accounts' not in f.read_text()
    result = usage.view(0, path=path, identity='fixture-mac')
    assert len(result['daily']) == 121 and result['start_date'] == first.isoformat()
    assert sum(m['total_tokens'] for m in result['models']) == 121 * 300
    assert result['additive'] is False and result['billed_cost_usd'] is None
    assert usage.view(0, path=path, identity='other')['daily'] == []
    with sqlite3.connect(path) as db:
        assert db.execute('select count(*) from snapshots').fetchone()[0] == 121


def test_batch_deadline_or_partial_failure_keeps_old_export_and_cache(tmp_path):
    directory = tmp_path / 'private'
    data = raw()
    def good(ocx, start, stop, *, deadline):
        return {**data, 'since':start, 'until':stop}
    producer.export_batches(directory, start_date='2026-11-23', end_date='2026-11-23', reader=good)
    target = next(directory.glob('*.json'))
    old = target.read_bytes()
    def partial(*args, **kwargs):
        return {**data, 'usageIncomplete':True}
    with pytest.raises(usage.InvalidSnapshot):
        producer.export_batches(directory, start_date='2026-11-23', end_date='2026-11-23', reader=partial)
    assert target.read_bytes() == old
    def offline(*args, **kwargs):
        raise TimeoutError('private body never printed')
    with pytest.raises(TimeoutError):
        producer.export_batches(directory, start_date='2026-11-23', end_date='2026-11-23', reader=offline)
    assert target.read_bytes() == old


@pytest.mark.parametrize('field', ['accounts','prompt','logs','secret'])
def test_v2_refuses_unapproved_fields_before_cache_creation(tmp_path, field):
    out = producer.envelope([dict(date='2026-11-23', response=usage.project_window(raw()))], 'fixture-mac')
    out['windows'][0]['response'][field] = ['DO_NOT_STORE']
    target = tmp_path / 'db'
    with pytest.raises(usage.InvalidSnapshot):
        usage.import_snapshot(out, path=target)
    assert not target.exists()


def test_actual_cli_pipe_is_bounded_and_suppresses_stderr(tmp_path, capsys):
    fake = tmp_path / 'ocx-fixture'
    fake.write_text('#!/usr/bin/env python3\nimport json,sys\nprint(json.dumps({"arguments":sys.argv[1:]}))\nprint("PRIVATE",file=sys.stderr)\n')
    fake.chmod(0o700)
    body = producer.cli_usage(str(fake), 1, 2, deadline=time.monotonic() + 3)
    assert body['arguments'] == ['usage','--range','all','--surface','all','--json','--since','1','--until','2']
    assert 'PRIVATE' not in capsys.readouterr().err
    fake.write_text('#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n')
    start = time.monotonic()
    with pytest.raises(TimeoutError):
        producer.cli_usage(str(fake), deadline=start + .1)
    assert time.monotonic() - start < 1


def test_cli_error_receipt_is_safe(tmp_path, capsys):
    assert producer.main(['--output-dir', str(tmp_path), '--ocx', '/not-an-executable']) == 1
    assert '/not-an-executable' not in capsys.readouterr().err


def test_duplicate_and_nonfinite_json_fields_are_not_valid_measurements():
    for text in ['{"totalTokens":1,"totalTokens":2}', '{"tokens":NaN}']:
        with pytest.raises(usage.InvalidSnapshot):
            usage.decode_json(text)


def test_minimized_fixture_is_the_real_allowlisted_contract():
    fixture = Path(__file__).parent / 'fixtures/opencodex-aggregate-v2.json'
    result = usage.normalize_export(usage.read_export(fixture))
    assert result[0] == 'fixture-mac' and result[1][0]['total_tokens'] == 300


def test_partial_missing_category_remains_unknown_over_whole_model_period(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED','1')
    first = raw()
    second = deepcopy(first)
    second['since'], second['until'] = usage.bounds('2026-11-22')
    second['models'][0].pop('cacheReadInputTokens')
    out = producer.envelope([dict(date='2026-11-22',response=usage.project_window(second)),
                             dict(date='2026-11-23',response=usage.project_window(first))], 'fixture-mac')
    path = tmp_path / 'own.sqlite'
    usage.import_snapshot(out, path=path)
    result = usage.view(0,path=path,identity='fixture-mac')
    assert result['models'][0]['cache_read_tokens'] is None
    assert result['daily'][1]['models'][0]['cache_read_tokens'] == 10
    assert result['daily'][0]['models'][0]['reported_counters']['cachedInputTokens'] == 10
