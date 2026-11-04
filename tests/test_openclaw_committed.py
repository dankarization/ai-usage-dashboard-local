"""Offline contract/fault tests: no SDK, CLI, transcript content or live DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import struct
import time

from fastapi.testclient import TestClient
import pytest

import local_display_service as service
import openclaw_collect as worker
import openclaw_committed as source
import openclaw_snapshot as snapshot
import openclaw_usage

NOW = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)


def quarter(rows):
    # Independent wire fixture: same quarter can have several model-owned vectors.
    columns = [[] for _ in range(49)]
    models = []
    for day, provider, model, counters in rows:
        q = len(columns[0])
        columns[0].append((day-1)*96)
        values = [*counters, sum(counters), .25, .1, .1, .05, 0, 0]
        for n in range(1, 25):
            columns[n].append(values[n-3] if 3 <= n < 14 else 0)
        models.append([provider, model])
        for col, value in zip(range(25, 29), (q, len(models)-1, 1, 0)):
            columns[col].append(value)
    codes = ''.join('d' if i in range(8, 13) else 'i' for i in range(49))
    ints = b''.join(struct.pack('<I', int(n)) for i, col in enumerate(columns) if codes[i] == 'i' for n in col)
    floats = b''.join(struct.pack('<d', float(n)) for i, col in enumerate(columns) if codes[i] == 'd' for n in col)
    blob = ints + b'\0' * (-len(ints) % 8) + floats
    header = dict(format=2, rows=[len(rows), len(rows), 0, 0, 0, 0],
                  columns=codes, models=models, tools=[], missingKeys=[])
    return json.dumps(header), blob


@pytest.fixture
def agents(tmp_path):
    root = tmp_path / 'agents'
    path = root / 'test/agent/openclaw-agent.sqlite'
    path.parent.mkdir(parents=True)
    key = 'sqlite:test:one:' + str(path)
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE cache_entries(scope TEXT,key TEXT,value_json TEXT,blob BLOB,updated_at INT,PRIMARY KEY(scope,key))')
        c.execute('CREATE TABLE secret_transcripts(content TEXT)')
        dates = ['2026-08-01', '2026-10-01', '2026-11-01']
        envelope = dict(version=11, format=1, scannedAt=NOW.timestamp()*1000-1000,
                        projection=dict(version=3, dates=dates))
        c.execute('INSERT INTO cache_entries VALUES(?,?,?,?,?)',
                  (source.ROLLUP, key, json.dumps(envelope), b'NEVER READ ROLLUP BODY', 1))
        for month, rows in [
            ('2026-08-01', [(8, 'openai', 'gpt-test', (1, 2, 3, 4))]),
            ('2026-10-01', [(7, 'openai', 'gpt-test', (10, 20, 30, 40)),
                           (30, 'openai', 'gpt-test', (100, 200, 300, 400))]),
            ('2026-11-01', [(1, 'openai', 'gpt-test', (1000, 2000, 3000, 4000)),
                           (5, 'openai', 'gpt-test', (10000, 20000, 30000, 40000))])]:
            header, blob = quarter(rows)
            qkey = json.dumps(key) + ':' + month
            c.execute('INSERT INTO cache_entries VALUES(?,?,?,?,?)', (source.PARTITION,qkey,'{}',b'NEVER READ PARTITION BODY',2))
            c.execute('INSERT INTO cache_entries VALUES(?,?,?,?,?)', (source.QUARTER,qkey,header,blob,2))
    return root


def test_exact_local_windows_and_categories(agents, tmp_path):
    data = source.collect(agents, now=NOW)
    output = tmp_path / 'snapshot.json'
    snapshot.atomic_json(output, data)
    for days, expected in [(1,100000), (7,111000), (30,111100), (90,111110)]:
        end = NOW.date()
        start = end - timedelta(days=days-1)
        result = snapshot.load(days, start.isoformat(), end.isoformat(), path=output, now=NOW)
        assert result['status'] == 'partial' and not result['snapshot_complete']
        assert result['response']['coverage']['requestedRangeCovered']
        entries = openclaw_usage.model_entries(result['response'], include_daily=True)
        assert entries[0]['totals']['total'] == expected
        assert entries[0]['totals']['input'] == expected // 10
        assert sum(d['input'] for d in entries[0]['daily']) == expected // 10
    assert data['coverage']['aggregateBytes'] < 10000


def test_owned_multi_model_and_derived_columns():
    # One quarter, two models: each has its OWN totals, never duplicate the quarter.
    columns = [[0] for _ in range(25)] + [[0,0],[0,1],[1,1],[1,1]] + [[2,3],[5,7],[0,0],[0,0],[0,0],[0,0],[0,0],[0,0],[0,0],[0,0],[0,0]] + [[] for _ in range(9)]
    codes = ['i'] * 49
    codes[33] = 's'  # totalTokens = input + output + cacheRead + cacheWrite
    ints = b''.join(struct.pack('<I', n) for i,col in enumerate(columns) if codes[i]=='i' for n in col)
    h = dict(format=2, rows=[1,2,2,0,0,0], columns=''.join(codes), models=[['p','a'],['p','b']])
    result = list(source.decode('2026-11-01', json.dumps(h), ints+b'\0'*(-len(ints)%8)))
    assert [r['totalTokens'] for r in result] == [7,10]


@pytest.mark.parametrize('fault', ['pair','version','blob','foreign','budget','deadline'])
def test_source_failure_never_replaces_last_good(agents, tmp_path, monkeypatch, fault):
    output = tmp_path / 'snapshot.json'
    data = source.collect(agents, now=NOW)
    snapshot.atomic_json(output, data)
    original = output.read_bytes()
    if fault in ('pair','version','blob','foreign'):
        p = next(agents.glob('*/agent/*.sqlite'))
        with sqlite3.connect(p) as c:
            if fault == 'pair': c.execute('UPDATE cache_entries SET updated_at=7 WHERE scope=?',(source.QUARTER,))
            if fault == 'version': c.execute('UPDATE cache_entries SET value_json=replace(value_json,\'"version": 11\',\'"version": 999\') WHERE scope=?',(source.ROLLUP,))
            if fault == 'blob': c.execute('UPDATE cache_entries SET blob=x\'00\' WHERE scope=?',(source.QUARTER,))
            if fault == 'foreign': c.execute('UPDATE cache_entries SET key=\'legacy.jsonl\' WHERE scope=?',(source.ROLLUP,))
    if fault == 'budget': monkeypatch.setattr(source, 'MAX_READ_BYTES', 1)
    def reader(root, deadline):
        return source.collect(root, now=NOW, deadline=0 if fault=='deadline' else deadline)
    with pytest.raises((ValueError, TimeoutError, struct.error)):
        worker.run(agents, output, now=10000, reader=reader)
    assert output.read_bytes() == original
    assert worker.run(agents, output, now=10001, reader=lambda *_a,**_k:pytest.fail('retry')) == 'rate_limited'


def test_singleton_restart_safe_backoff_and_cancel(tmp_path):
    output = tmp_path / 'snapshot.json'
    import threading
    began, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        began.set()
        assert release.wait(3)
        raise TimeoutError('cancelled')
    def attempt():
        with pytest.raises(TimeoutError):
            worker.run(tmp_path, output, now=10000, reader=blocked)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(attempt)
        assert began.wait(1)
        assert worker.run(tmp_path, output, now=10000) == 'locked'
        release.set()
        future.result()
    state = json.loads(output.with_suffix('.state.json').read_text())
    assert state['next_allowed_at'] == 13600
    assert not output.exists()
    assert worker.run(tmp_path, output, now=11000) == 'rate_limited'


def test_atomic_write_failure_keeps_previous_and_removes_temp(tmp_path, monkeypatch):
    path = tmp_path / 'snapshot.json'
    snapshot.atomic_json(path, {'old': 1})
    def fail(*_): raise OSError('disk full')
    monkeypatch.setattr(snapshot.os, 'replace', fail)
    with pytest.raises(OSError): snapshot.atomic_json(path, {'new': 2})
    assert json.loads(path.read_text()) == {'old': 1}
    assert list(tmp_path.iterdir()) == [path]


def test_readers_never_call_upstream_even_many_tabs(agents, tmp_path, monkeypatch):
    output = tmp_path / 'snapshot.json'
    monkeypatch.setenv('OPENCLAW_AGGREGATE_SNAPSHOT', str(output))
    monkeypatch.setattr('auto_usage.load_env', lambda:None)
    monkeypatch.setattr(service, 'load_env', lambda:None)
    monkeypatch.setattr('auto_usage.get_model_date_range', lambda d:
        ((NOW.date()-timedelta(days=d-1)).isoformat(), NOW.date().isoformat(), None, None))
    def forbidden(*args, **kwargs): pytest.fail('HTTP attempted collection/spawn')
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', forbidden)
    monkeypatch.setattr(worker, 'collect', forbidden)
    import subprocess
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    client = TestClient(service.app)
    for state in ('missing','valid','corrupt','stale'):
        if state in ('valid','stale'):
            data = source.collect(agents, now=NOW)
            if state=='stale': data['collectedAt'] = (NOW-timedelta(days=2)).isoformat()
            snapshot.atomic_json(output,data)
        if state=='corrupt': output.write_text('{')
        with ThreadPoolExecutor(8) as pool:
            responses = list(pool.map(lambda n: client.get('/api/v1/model-breakdown',
                params=dict(days=[1,7,30,90][n%4],source='openclaw',refresh=True)), range(100)))
        assert all(r.status_code==200 for r in responses)
        assert all(not r.json()['meta']['openclaw_refreshing'] for r in responses)
    assert not output.with_suffix('.state.json').exists()


def test_midnight_stale_and_corrupt_are_not_complete(agents, tmp_path):
    output = tmp_path / 'snapshot.json'
    snapshot.atomic_json(output, source.collect(agents, now=NOW))
    result = snapshot.load(7,'2026-10-31','2026-11-06',path=output,now=NOW+timedelta(days=1))
    assert result['status']=='stale'
    assert not result['response']['coverage']['requestedRangeCovered']
    output.write_text('{}')
    assert snapshot.load(7,'2026-10-30','2026-11-05',path=output)['status']=='unavailable'


def test_direct_unsupervised_entry_refused(monkeypatch):
    monkeypatch.delenv('INVOCATION_ID',raising=False)
    with pytest.raises(RuntimeError,match='systemctl'): worker.require_supervisor()


def test_legacy_numeric_rows_and_ambiguous_instances_are_explicit(agents):
    p = next(agents.glob('*/agent/*.sqlite'))
    with sqlite3.connect(p) as c:
        key, header, blob, updated = c.execute('SELECT key,value_json,blob,updated_at FROM cache_entries WHERE scope=?',(source.ROLLUP,)).fetchone()
        # A second artifact claims the same instance; neither may be added twice.
        alias = str(p.parent.parent/'sessions/one.jsonl.deleted.old')
        c.execute('INSERT INTO cache_entries VALUES(?,?,?,?,?)',(source.ROLLUP,alias,header,blob,updated))
        old = json.loads(header);old['version']=8
        c.execute('INSERT INTO cache_entries VALUES(?,?,?,?,?)',(source.ROLLUP,'old',json.dumps(old),blob,updated))
        # Keep one other independent, supported instance, with empty months.
        h = json.loads(header);h['projection']['dates']=[]
        c.execute('INSERT INTO cache_entries VALUES(?,?,?,?,?)',(source.ROLLUP,key.replace(':one:',':two:'),json.dumps(h),blob,updated))
    data=source.collect(agents,now=NOW)
    assert data['daily']==[]
    assert data['coverage']['excludedAmbiguousRollups']==2
    assert data['coverage']['excludedOldFormatRollups']==1
    assert not data['coverage']['committedRowsComplete']


def test_inconsistent_reported_categories_are_preserved_not_invented(agents,tmp_path):
    data=source.collect(agents,now=NOW)
    data['daily'][0]['totalTokens'] += 77
    p=tmp_path/'snapshot.json'
    snapshot.atomic_json(p,data)
    assert snapshot.read(p)['daily'][0]['totalTokens']==data['daily'][0]['totalTokens']


def test_cleanup_incomplete_blocks_next_collection(tmp_path, monkeypatch):
    output=tmp_path/'snapshot.json'
    snapshot.atomic_json(output.with_suffix('.state.json'),dict(blocked=True))
    with pytest.raises(RuntimeError,match='cleanup'):
        worker.run(tmp_path,output,reader=lambda *_a,**_k:pytest.fail('must not read'))


def test_multiple_fresh_http_workers_never_collect(tmp_path):
    import subprocess,sys
    script='''
import subprocess
import local_display_service as service
import openclaw_usage
from fastapi import Response

def forbidden(*a,**k): raise AssertionError('spawn/upstream')
openclaw_usage.fetch_usage=forbidden
subprocess.Popen=forbidden
service.load_env=lambda:None
for n in range(100):
    result=service.model_breakdown(Response(), days=[1,7,30,90][n%4],refresh=True,source='openclaw')
    assert result['meta']['openclaw_status']=='unavailable'
assert not any(t.name.startswith('dashboard-openclaw') for t in __import__('threading').enumerate())
'''
    def run(_):
        return subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True,timeout=15)
    with ThreadPoolExecutor(3) as pool:
        for result in pool.map(run,range(3)):
            assert result.returncode==0,result.stderr


def test_display_get_and_update_cannot_activate_openclaw(monkeypatch):
    def forbidden(*_a,**_k): pytest.fail('display route attempted OpenClaw collection')
    monkeypatch.setattr(openclaw_usage,'fetch_usage',forbidden)
    monkeypatch.setattr(worker,'collect',forbidden)
    # Other provider exports are outside this feature: isolate only their generator.
    monkeypatch.setattr(service,'build_latest_dashboard_payload',lambda **_k:dict(meta={},summary={},daily=[]))
    monkeypatch.setattr(service,'_cached_payload',None)
    client=TestClient(service.app)
    for path in ('/','/dashboard','/health','/openapi.json','/token_usage.json'):
        assert client.get(path).status_code==200
    assert client.post('/api/v1/display/update',json=dict(reason='manual',view='30d',device_id='offline-test')).status_code==200
