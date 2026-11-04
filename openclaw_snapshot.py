"""Dashboard-owned aggregate artifact. Readers never collect or repair anything."""
from __future__ import annotations

from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile

FIELDS = ('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens',
          'totalCost', 'inputCost', 'outputCost', 'cacheReadCost', 'cacheWriteCost',
          'missingCostEntries')
MAX_BYTES = 8 * 1024 * 1024


def snapshot_path():
    return Path(os.environ.get('OPENCLAW_AGGREGATE_SNAPSHOT',
                str(Path(__file__).resolve().parent / 'data/openclaw-committed.json')))


def validate(data):
    if data['schema'] != 1 or data['source'] != 'openclaw-committed-quarters-v2':
        raise ValueError('Unsupported committed aggregate schema')
    start, end = date.fromisoformat(data['startDate']), date.fromisoformat(data['endDate'])
    if not 0 <= (end - start).days <= 89:
        raise ValueError('Invalid aggregate range')
    stamp = datetime.fromisoformat(data['collectedAt'])
    if stamp.tzinfo is None or not isinstance(data['coverage'], dict):
        raise ValueError('Invalid aggregate provenance')
    coverage = data['coverage']
    if coverage.get('complete') is not False or coverage.get('strategy') != 'committed-only-no-refresh':
        raise ValueError('Committed-only data cannot certify live coverage')
    for key in ('oldestComputedAtUtc', 'newestComputedAtUtc'):
        value = coverage.get(key)
        if value is not None and (not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None):
            raise ValueError('Invalid producer timestamp')
    seen = set()
    if not isinstance(data['daily'], list) or len(data['daily']) > 20000:
        raise ValueError('Aggregate row limit exceeded')
    for row in data['daily']:
        identity = tuple(row[k] for k in ('date', 'provider', 'model'))
        if identity in seen or any(not isinstance(v, str) or len(v) > 256 for v in identity):
            raise ValueError('Invalid or duplicate model day')
        seen.add(identity)
        if not start <= date.fromisoformat(row['date']) <= end:
            raise ValueError('Model day outside aggregate range')
        for field in FIELDS:
            n = row[field]
            if isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) or n < 0:
                raise ValueError('Invalid aggregate counter')
            if field in (*FIELDS[:5], 'missingCostEntries') and (int(n) != n or n > 2**53 - 1):
                raise ValueError('Invalid token counter')
        # OpenClaw stores independently reported totals. Some provider counters
        # do not add up to the reported total; preserve them, never rebalance.
    return data


def read(path=None):
    # One bounded owned artifact; no mkdir, migration, glob or fallback.
    with Path(path or snapshot_path()).open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Aggregate snapshot too large')
    return validate(json.loads(raw))


def atomic_json(path, data):
    path = Path(path)
    raw = json.dumps(data, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > MAX_BYTES:
        raise ValueError('Aggregate snapshot too large')
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', delete=False) as handle:
            name = handle.name
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
        name = None
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if name is not None:
            os.unlink(name)


def load(days, start_date, end_date, *, path=None, now=None):
    result = dict(status='unavailable', response=None, cached_at_utc=None,
                  gateway_cache_status=None, upstream_status='unavailable', error=None,
                  snapshot_written_at_utc=None, snapshot_complete=False,
                  source_at_utc=None, refreshing=False)
    try:
        data = read(path)
        if (date.fromisoformat(end_date) - date.fromisoformat(start_date)).days != days - 1:
            raise ValueError('Invalid requested range')
        covers = data['startDate'] <= start_date <= end_date <= data['endDate']
        rows = [r for r in data['daily'] if start_date <= r['date'] <= end_date]
        if start_date < data['startDate'] or end_date < data['startDate'] or start_date > data['endDate']:
            raise ValueError('No snapshot coverage for requested range')
        by_model = {}
        daily = []
        for row in rows:
            key = row['provider'], row['model']
            total = by_model.setdefault(key, dict.fromkeys(FIELDS, 0))
            for field in FIELDS:
                total[field] += row[field]
            daily.append({**row, 'tokens': row['totalTokens'], 'cost': row['totalCost']})
        age = ((now or datetime.now(timezone.utc)) - datetime.fromisoformat(data['collectedAt'])).total_seconds()
        coverage = {**data['coverage'], 'requestedRangeCovered': covers,
                    'snapshotStartDate': data['startDate'], 'snapshotEndDate': data['endDate'],
                    'collectionStale': age < 0 or age > 7200}
        response = {'aggregates': {'byModel': [dict(provider=p, model=m, totals=t)
                        for (p, m), t in sorted(by_model.items())], 'modelDaily': daily},
                    'startDate': start_date, 'endDate': end_date, 'coverage': coverage,
                    'cacheStatus': {'status': 'partial'}}
        result.update(status='stale' if coverage['collectionStale'] or not covers else 'partial',
                      response=response, cached_at_utc=data['collectedAt'], upstream_status='committed_only',
                      source_at_utc=data['coverage'].get('oldestComputedAtUtc'),
                      error='Committed aggregates only; live activity coverage is not verified')
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        result['error'] = 'Committed aggregate snapshot missing, invalid, or outside requested range'
    return result
