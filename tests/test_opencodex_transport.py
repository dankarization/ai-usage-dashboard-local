"""Local fixture HTTP service only; never calls a real OpenCodex instance."""
from contextlib import contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit

import pytest

import opencodex_collect as collector
import opencodex_usage as usage

FIXTURE = Path(__file__).parent / 'fixtures/opencodex-usage-v1.json'


@contextmanager
def fixture_server(reply):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            try:
                reply(self)
            except (OSError, BrokenPipeError):
                pass
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def serve_json(handler, body, *, status=200, headers=None):
    raw = json.dumps(body).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(raw)))
    for key, value in (headers or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(raw)


def test_loopback_uses_only_exact_readonly_custom_window_and_ignores_proxy_env(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    monkeypatch.setenv('HTTP_PROXY', 'http://unreachable.invalid:1234')
    export = json.loads(FIXTURE.read_text())
    expected = usage.project_window(export['windows'][0]['response'])
    with fixture_server(lambda h: serve_json(h, expected)) as (url, requests):
        result = collector.collect(base_url=url, path=tmp_path / 'owned.sqlite', days=1,
                                   identity='fixture-mac', end_date='2026-11-23')
    assert result['days_changed'] == 1
    assert len(requests) == 1
    path, headers = requests[0]
    parsed = urlsplit(path)
    assert parsed.path == '/api/usage'
    assert parse_qs(parsed.query) == {'range':['all'], 'surface':['all'],
        'since':[str(expected['since'])], 'until':[str(expected['until'])]}
    assert not any(k.lower() in ('authorization', 'cookie', 'x-api-key') for k in headers)


@pytest.mark.parametrize('url', [
    'http://localhost:1234', 'http://example.invalid:1234', 'https://127.0.0.1:1234',
    'http://127.0.0.1', 'http://127.0.0.1:1234/api/usage', 'http://127.0.0.1:1234?token=bad',
    'http://token@127.0.0.1:1234', 'http://127.0.0.1:1234#secret', 'file:///tmp/export',
])
def test_non_facade_targets_rejected_without_connecting(url, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Must not open a transport')
    monkeypatch.setattr(collector, '_fetch', forbidden)
    with pytest.raises(ValueError):
        collector.fetch_export(url, '2026-11-23', '2026-11-23')


@pytest.mark.parametrize('failure', ['partial', '500', 'redirect', 'html', 'oversize', 'truncated', 'invalid_json'])
def test_failed_collection_preserves_existing_cache(tmp_path, monkeypatch, failure):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    export = json.loads(FIXTURE.read_text())
    path = tmp_path / 'owned.sqlite'
    usage.import_snapshot(export, path=path)
    saved = path.read_bytes()
    body = deepcopy(export['windows'][0]['response'])
    body['generatedAt'] += 1
    def reply(h):
        if failure == 'partial':
            body['usageIncomplete'] = True
            return serve_json(h, body)
        if failure == '500':
            return serve_json(h, {'error':'private upstream body'}, status=500)
        if failure == 'redirect':
            return serve_json(h, {}, status=302, headers={'Location':'http://example.invalid/admin'})
        h.send_response(200)
        h.send_header('Content-Type', 'text/html' if failure == 'html' else 'application/json')
        raw = json.dumps(body).encode() if failure != 'invalid_json' else b'{broken'
        h.send_header('Content-Length', str(collector.RESPONSE_LIMIT + 1 if failure == 'oversize' else
                                          len(raw) + 100 if failure == 'truncated' else len(raw)))
        h.end_headers()
        if failure != 'oversize':
            h.wfile.write(raw)
    with fixture_server(reply) as (url, requests):
        with pytest.raises((ValueError, OSError)):
            collector.collect(base_url=url, path=path, days=1,
                              identity='fixture-mac', end_date='2026-11-23', timeout=1)
    assert len(requests) == 1  # No follow, retry or fallback to logs/admin.
    assert path.read_bytes() == saved


def test_slow_trickle_has_a_whole_collection_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    def slow(h):
        h.send_response(200)
        h.send_header('Content-Type', 'application/json')
        h.send_header('Content-Length', '10000')
        h.end_headers()
        for _ in range(40):
            h.wfile.write(b' ')
            h.wfile.flush()
            time.sleep(.02)
    path = tmp_path / 'owned.sqlite'
    with fixture_server(slow) as (url, _):
        start = time.monotonic()
        with pytest.raises(TimeoutError):
            collector.collect(base_url=url, path=path, days=1, end_date='2026-11-23', timeout=.12)
        assert time.monotonic() - start < .5
    assert not path.exists()


def test_multi_day_import_waits_for_every_valid_day(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    body = usage.project_window(json.loads(FIXTURE.read_text())['windows'][0]['response'])
    count = 0
    def reply(h):
        nonlocal count
        count += 1
        if count == 2:
            return serve_json(h, {'error':'read_failed'}, status=500)
        window = deepcopy(body)
        params = parse_qs(urlsplit(h.path).query)
        window.update(since=int(params['since'][0]), until=int(params['until'][0]))
        return serve_json(h, window)
    path = tmp_path / 'owned.sqlite'
    with fixture_server(reply) as (url, _):
        with pytest.raises(ValueError):
            collector.collect(base_url=url, path=path, days=2, end_date='2026-11-23')
    assert count == 2
    assert not path.exists()


def test_slow_http_headers_are_also_deadline_bounded(tmp_path, monkeypatch):
    import http.client
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    def slow_headers(h):
        h.wfile.write(b'HTTP/1.0 200 OK\r\nX-Slow: ')
        h.wfile.flush()
        for _ in range(40):
            h.wfile.write(b'a')
            h.wfile.flush()
            time.sleep(.02)
    with fixture_server(slow_headers) as (url, _):
        start = time.monotonic()
        with pytest.raises((OSError, ValueError, http.client.HTTPException)):
            collector.collect(base_url=url, path=tmp_path / 'owned.sqlite', days=1,
                              end_date='2026-11-23', timeout=.12)
        assert time.monotonic() - start < .5


def test_direct_local_api_projects_raw_accounts_without_persistence_or_logging(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED','1')
    raw = json.loads(FIXTURE.read_text())['windows'][0]['response']
    raw['accounts'][0]['accountLogLabel'] = 'PRIVATE_ACCOUNT_SENTINEL'
    raw.update(logs=['PRIVATE_LOG_SENTINEL'], token='PRIVATE_AUTH_SENTINEL')
    cache = tmp_path / 'owned.sqlite'
    with fixture_server(lambda h: serve_json(h, raw)) as (url, requests):
        result = collector.collect(base_url=url, path=cache, days=1,
                                   identity='fixture-mac', end_date='2026-11-23')
    assert result['days_changed'] == 1
    assert b'PRIVATE_' not in cache.read_bytes()
    assert usage.view(1,path=cache,identity='fixture-mac')['accounts'] == []
    captured = capsys.readouterr()
    assert 'PRIVATE_' not in captured.out + captured.err
    assert len(requests) == 1
    assert not any(k.lower() in ('authorization','cookie','x-api-key','x-opencodex-api-key') for k in requests[0][1])


@pytest.mark.parametrize('status', [401,403,503])
def test_auth_denial_never_discovers_or_forwards_credentials(tmp_path, monkeypatch, capsys, status):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED','1')
    monkeypatch.setenv('OPENCODEX_ADMIN_AUTH_TOKEN','PRIVATE_AUTH_SENTINEL')
    export = json.loads(FIXTURE.read_text())
    cache = tmp_path / 'owned.sqlite'
    usage.import_snapshot(export, path=cache)
    original = cache.read_bytes()
    with fixture_server(lambda h: serve_json(h, {'error':'PRIVATE_AUTH_SENTINEL'}, status=status)) as (url, requests):
        assert collector.main(['--base-url',url,'--db',str(cache),'--source-id','fixture-mac',
                               '--days','1','--end-date','2026-11-23']) == 1
    assert cache.read_bytes() == original
    captured = capsys.readouterr()
    assert 'PRIVATE_' not in captured.out + captured.err
    assert len(requests) == 1  # no auth refresh/retry, CLI fallback or side routes
    assert not any(k.lower() in ('authorization','cookie','x-api-key','x-opencodex-api-key') for k in requests[0][1])
