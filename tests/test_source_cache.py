"""Read coalescing, refresh fencing, and exact-window cache identity."""
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from fastapi.testclient import TestClient

from source_cache import SourceCache


def test_concurrent_forced_reads_are_single_flight_and_keep_fetch_time():
    cache = SourceCache()
    started, release = threading.Event(), threading.Event()
    calls = []
    def collect():
        calls.append(1)
        started.set()
        assert release.wait(2)
        return {'meta': {'generated_at_utc': '2026-01-01T00:00:00Z'}}
    with ThreadPoolExecutor(max_workers=8) as pool:
        first = pool.submit(cache.get, ('window', 7), collect, force=True)
        assert started.wait(2)
        rest = [pool.submit(cache.get, ('window', 7), collect, force=True) for _ in range(7)]
        time.sleep(.05)
        release.set()
        results = [first.result(2), *[f.result(2) for f in rest]]
    assert len(calls) == 1
    assert sum(status == 'fresh' for _, status in results) == 1
    assert all(value['meta']['generated_at_utc'] == '2026-01-01T00:00:00Z' for value, _ in results)
    cache.get(('window', 7), collect, force=True)
    assert len(calls) == 2  # a later explicit refresh is not swallowed


def test_invalidation_fences_old_inflight_completion():
    cache = SourceCache()
    started, release = threading.Event(), threading.Event()
    def old():
        started.set()
        assert release.wait(2)
        return {'value': 'old'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(cache.get, 'key', old)
        assert started.wait(2)
        cache.invalidate()
        assert cache.get('key', lambda: {'value': 'new'})[0]['value'] == 'new'
        release.set()
        assert pending.result(2)[0]['value'] == 'old'
    assert cache.get('key', lambda: {'value': 'wrong'}) == ({'value': 'new'}, 'cached')


def test_expiry_failure_backoff_and_bounded_identity(monkeypatch):
    import source_cache
    clock = [100.]
    monkeypatch.setattr(source_cache.time, 'monotonic', lambda: clock[0])
    cache = SourceCache(ttl=30, failure_ttl=5, limit=2)
    calls = []
    def unavailable():
        calls.append(1)
        return {'meta': {'openclaw_status': 'unavailable'}}
    cache.get(('day-one', 7, True), unavailable)
    cache.get(('day-one', 7, True), unavailable)
    assert len(calls) == 1
    clock[0] += 6
    cache.get(('day-one', 7, True), unavailable)
    assert len(calls) == 2
    cache.get(('day-two', 7, True), unavailable)
    cache.get(('day-two', 1, False), unavailable)
    assert len(cache._entries) == 2
    assert ('day-one', 7, True) not in cache._entries


def test_api_period_cache_force_and_quota_isolation(monkeypatch):
    import local_display_service as service
    calls = []
    monkeypatch.setattr(service, 'load_env', lambda: None)
    def collect(**kwargs):
        calls.append(kwargs)
        return {'meta': {'days': kwargs['days'], 'generated_at_utc': '2026-01-01T00:00:00Z'}, 'models': []}
    monkeypatch.setattr(service, 'build_model_breakdown', collect)
    client = TestClient(service.app)
    url = '/api/v1/model-breakdown?days=7&source=openclaw'
    assert client.get(url).headers['X-Dashboard-Source-Cache'] == 'fresh'
    assert client.get(url).headers['X-Dashboard-Source-Cache'] == 'cached'
    assert len(calls) == 1
    assert client.get(url + '&refresh=true').headers['X-Dashboard-Source-Cache'] == 'fresh'
    client.get('/api/v1/model-breakdown?days=1&source=openclaw')
    client.get('/api/v1/model-breakdown?days=7&daily=false&source=openclaw')
    assert len(calls) == 4
