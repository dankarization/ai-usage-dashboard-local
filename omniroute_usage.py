"""Read retained OmniRoute serving-route aggregates; never read message bodies.

Bucket costs are router-wide observations, not per-agent allocations. Current
NordRouter retail prices are estimates, not historical invoices. Parent names
and nested paths come only from historical call metadata, never today's combos.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time as day_time, timedelta, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
from zoneinfo import ZoneInfo

import nordrouter_usage

ROOT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Tbilisi')
TTL = 300


def is_bucket(row):
    return str(row.get('provider', '')).lower() == 'omniroute'


def bucket_key(model):
    return 'omniroute/' + model.removeprefix('omniroute/')


def bounds(start, end, tz=TZ):
    """Inclusive local calendar dates -> half-open UTC instants (DST safe)."""
    return tuple(datetime.combine(d, day_time(), tz).astimezone(timezone.utc).isoformat()
                 for d in (date.fromisoformat(start), date.fromisoformat(end) + timedelta(days=1)))


class Client:
    """Opt-in existing Docker access; aggregate-only cross-process 5m cache.

    A single maximum-window snapshot serves all UI periods. Failed refreshes
    back off too. No poller, credentials, raw records, or unbounded window cache.
    """
    def __init__(self, container=None, cache_dir=None):
        self.container = os.environ.get('OMNIROUTE_USAGE_CONTAINER', '') if container is None else container
        identity = hashlib.sha256(self.container.encode()).hexdigest()[:16]
        self.root = Path(cache_dir or ROOT / 'data' / 'omniroute') / identity

    def _read(self, start, end):
        script = (ROOT / 'scripts' / 'omniroute-aggregate.cjs').read_text()
        result = subprocess.run(['docker', 'exec', '-i', self.container, 'node', '-e', script],
                                input=json.dumps(dict(zip(('start', 'end'), bounds(start, end)))),
                                capture_output=True, text=True, check=True, timeout=20)
        body = json.loads(result.stdout)
        if not isinstance(body.get('rows'), list) or not isinstance(body.get('providers'), dict):
            raise ValueError('Invalid aggregate shape')
        return body

    def get(self, end):
        if not self.container:
            return None, 'not_configured', None
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', self.container):
            return None, 'unavailable', None
        if self.root.is_symlink() or self.root.parent.is_symlink():
            return None, 'unavailable', None
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            path = self.root / 'aggregate.json'
            try:
                cache = json.loads(path.read_text())
            except (OSError, ValueError):
                cache = {}
            now = time.time()
            if now >= cache.get('retry_at', 0):
                cache['retry_at'] = now + TTL
                try:
                    first = (date.fromisoformat(end) - timedelta(days=89)).isoformat()
                    cache.update(data=self._read(first, end), end=end,
                                 fetched_at=datetime.now(timezone.utc).isoformat(), failed=False)
                except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
                    cache['failed'] = True
                temp = path.with_suffix('.tmp')
                temp.write_text(json.dumps(cache))
                temp.replace(path)
            stale = cache.get('failed') or cache.get('end') != end
            return cache.get('data'), ('stale' if stale else 'ok') if cache.get('data') else 'unavailable', cache.get('fetched_at')


def price_id(model, prices):
    """Exact ID or one unambiguous catalog namespace. Never strip suffixes."""
    if model in prices:
        return model
    matches = [key for key in prices if key.rsplit('/', 1)[-1] == model]
    return matches[0] if len(matches) == 1 else None


def route_cost(tokens, price):
    if price is None:
        return None
    total = 0.0
    for category, field in [('input', 'input_usd'), ('output', 'output_usd'),
                            ('cache_read', 'cache_read_usd'), ('cache_write', 'cache_write_usd')]:
        n = tokens[category]
        rate = price.get(field)
        if n and rate is None:
            return None
        total += n * (rate or 0) / 1_000_000
    return total


def aggregate(body, pricing, start, end, *, status='ok', fetched_at=None, pricing_stale=False, include_daily=True):
    first, last = (datetime.fromisoformat(x) for x in bounds(start, end))
    prices = {row['id']: row for row in (pricing or {}).get('data', [])}
    buckets = {}
    for row in body['rows']:
        if row.get('date'):
            day = date.fromisoformat(row['date']).isoformat()
            if not start <= day <= end:
                continue
        else:  # bounded compatibility with an existing pre-day-grouped cache
            stamp = datetime.fromisoformat(row['timestamp'].replace('Z', '+00:00'))
            if not first <= stamp < last:
                continue
            day = stamp.astimezone(TZ).date().isoformat()
        # Empty errors/outer combo placeholders do not represent used models.
        used = (row.get('prompt') or 0) + (row.get('output') or 0)
        if not used and not 200 <= row['status'] < 300:
            continue
        name = row['bucket']
        bucket = buckets.setdefault(name, [])
        prompt, output = row.get('prompt') or 0, row.get('output') or 0
        read, write = row.get('cache_read') or 0, row.get('cache_write') or 0
        valid = not row.get('invalid') and (used > 0 or row.get('usage_provenance') == 'reported')
        tokens = {'input': max(0, prompt - read - write), 'output': max(0, output),
                  'cache_read': max(0, read), 'cache_write': max(0, write), 'total': max(0, prompt + output)}
        nord = body['providers'].get(row['provider'])
        exact = price_id(row['model'], prices) if nord is True else None
        cost = route_cost(tokens, prices.get(exact)) if valid else None
        bucket.append({'date': day,
                       'provider': row['provider'], 'model': row['model'], 'price_id': exact,
                       'route_path': row.get('route_path'), 'usage_provenance': row.get('usage_provenance'),
                       'requests': row['requests'], 'totals': tokens,
                       'cost_usd': cost, 'nordrouter': nord,
                       'unknown_reason': None if cost is not None else ('invalid_or_missing_usage' if not valid else 'unverified_provider_origin' if nord is not True else 'no_exact_tariff')})
    entries = []
    oldest = (body.get('history') or {}).get('first')
    limited = not oldest or datetime.fromisoformat(oldest.replace('Z', '+00:00')) > first
    source_days = defaultdict(lambda: {'tokens': 0, 'known_cost_usd': 0.0, 'unpriced_requests': 0})
    observed_tokens = 0
    known_cost = 0.0
    unpriced = 0
    for name, routes in sorted(buckets.items()):
        totals = {field: sum(r['totals'][field] for r in routes) for field in routes[0]['totals']}
        known = sum(r['cost_usd'] for r in routes if r['cost_usd'] is not None)
        missing = sum(r['requests'] for r in routes if r['cost_usd'] is None)
        daily = []
        for day in sorted({r['date'] for r in routes}):
            rr = [r for r in routes if r['date'] == day]
            unknown = sum(r['requests'] for r in rr if r['cost_usd'] is None)
            dd = {field: sum(r['totals'][field] for r in rr) for field in totals}
            daily.append({'date': day, **dd, 'cost_usd': None if unknown else sum(r['cost_usd'] for r in rr),
                          'known_cost_usd': sum(r['cost_usd'] for r in rr if r['cost_usd'] is not None),
                          'unpriced_requests': unknown})
        for r in routes:
            # All router observations are non-additive. NordRouter invoices
            # already include these routes; agent token counts remain unchanged.
            observed_tokens += r['totals']['total']
            dd = source_days[r['date']]
            dd['tokens'] += r['totals']['total']
            if r['cost_usd'] is None:
                unpriced += r['requests']
                dd['unpriced_requests'] += r['requests']
            else:
                known_cost += r['cost_usd']
                dd['known_cost_usd'] += r['cost_usd']
        # Preserve exact historical routes, but consolidate daily SQL groups
        # for a bounded, useful response (no individual request identifiers).
        grouped = {}
        for r in routes:
            key = tuple(r[k] for k in ('provider', 'model', 'price_id', 'route_path', 'nordrouter', 'unknown_reason'))
            target = grouped.setdefault(key, {**r, 'requests': 0, 'totals': {f: 0 for f in totals}, 'cost_usd': 0.0 if r['cost_usd'] is not None else None, 'daily': []})
            if target['usage_provenance'] != r['usage_provenance']:
                target['usage_provenance'] = 'mixed'
            if include_daily:
                day = next((d for d in target['daily'] if d['date'] == r['date']), None)
                if day is None:
                    day = {'date': r['date'], 'totals': {f: 0 for f in totals}, 'requests': 0,
                           'cost_usd': 0.0 if r['cost_usd'] is not None else None}
                    target['daily'].append(day)
                day['requests'] += r['requests']
                for field in totals:
                    day['totals'][field] += r['totals'][field]
                if r['cost_usd'] is not None:
                    day['cost_usd'] += r['cost_usd']
            target.pop('date', None)
            target['requests'] += r['requests']
            for field in totals:
                target['totals'][field] += r['totals'][field]
            if r['cost_usd'] is not None:
                target['cost_usd'] += r['cost_usd']
        entries.append({'source': 'omniroute', 'provider': 'omniroute', 'model': name,
                        'model_key': bucket_key(name), 'totals': totals,
                        'daily': daily if include_daily else [], 'cost_usd': None if missing else known,
                        'cost_kind': 'estimated' if not missing else 'partial' if any(r['cost_usd'] is not None for r in routes) else 'unknown',
                        'bucket_usage': {'scope': 'router-wide; not per-agent', 'known_cost_usd': known,
                            'priced_requests': sum(r['requests'] for r in routes) - missing,
                            'unpriced_requests': missing, 'routes': list(grouped.values()),
                            'status': status, 'retention_limited': limited, 'retained_from': oldest,
                            'fetched_at': fetched_at, 'history_complete': False,
                            'pricing_generated_at': (pricing or {}).get('generated_at'),
                            'pricing_stale': pricing_stale,
                            'price_basis': 'current exact NordRouter retail tariffs; not historical billing',
                            'history_basis': 'retained call metadata; gaps/deleted history cannot be recovered'}})
    return {'models': entries, 'status': status, 'source': {
        'tokens': observed_tokens, 'estimated_cost_usd': known_cost if not unpriced else None,
        'known_cost_usd': known_cost, 'unpriced_models': sum(any(r['cost_usd'] is None for r in m['bucket_usage']['routes']) for m in entries),
        'complete': status == 'ok' and not limited and not unpriced and not pricing_stale, 'retention_limited': limited,
        'history_complete': False, 'retained_from': oldest, 'fetched_at': fetched_at,
        'status': status, 'pricing_stale': pricing_stale,
        'additive': False, 'accounting': 'router-wide observations only; never added to overview or NordRouter account totals',
    }, 'daily': [{'date': day, **v, 'amount_usd': None if v['unpriced_requests'] else v['known_cost_usd']} for day, v in sorted(source_days.items())]}


def collect(start, end, *, include_daily=True, client=None, price_client=None):
    try:
        body, status, fetched_at = (client or Client()).get(end)
        if body is None:
            return {'models': [], 'daily': [], 'status': status,
                    'source': {'tokens': 0, 'complete': status == 'not_configured', 'status': status}}
        key = os.environ.get('NORDROUTER_API_KEY', '')
        pricing, stale = (price_client or nordrouter_usage.Client(key)).get('pricing') if key or price_client else (None, True)
        return aggregate(body, pricing, start, end, status=status, fetched_at=fetched_at,
                         pricing_stale=stale, include_daily=include_daily)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {'models': [], 'daily': [], 'status': 'unavailable',
                'source': {'tokens': 0, 'complete': False, 'status': 'unavailable'}}
