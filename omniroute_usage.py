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

import nordrouter_usage

ROOT = Path(__file__).resolve().parent
TZ = timezone.utc
TTL = 300
AGGREGATE_VERSION = 2  # UTC days: never reuse old local-day snapshots.


def is_bucket(row):
    return str(row.get('provider', '')).lower() == 'omniroute'


def bucket_key(model):
    return 'omniroute/' + model.removeprefix('omniroute/')


def bounds(start, end, tz=TZ):
    """Inclusive calendar dates -> half-open UTC instants (UTC by default)."""
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
            if cache.get('version') != AGGREGATE_VERSION:
                cache = {}
            if now >= cache.get('retry_at', 0) or (cache.get('end') != end and not cache.get('failed')):
                cache['retry_at'] = now + TTL
                cache['version'] = AGGREGATE_VERSION
                try:
                    first = (date.fromisoformat(end) - timedelta(days=89)).isoformat()
                    cache.update(data=self._read(first, end), end=end, version=AGGREGATE_VERSION,
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
                       # Estimated/error prompt sizes are not reported consumption.
                       'reported_totals': tokens if valid and row.get('usage_provenance') == 'reported' else {f: 0 for f in tokens},
                       'unverified_requests': 0 if valid and row.get('usage_provenance') == 'reported' else row['requests'],
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
            target = grouped.setdefault(key, {**r, 'requests': 0, 'totals': {f: 0 for f in totals}, 'reported_totals': {f: 0 for f in totals}, 'unverified_requests': 0, 'cost_usd': 0.0 if r['cost_usd'] is not None else None, 'daily': []})
            if target['usage_provenance'] != r['usage_provenance']:
                target['usage_provenance'] = 'mixed'
            if include_daily:
                day = next((d for d in target['daily'] if d['date'] == r['date']), None)
                if day is None:
                    day = {'date': r['date'], 'totals': {f: 0 for f in totals}, 'reported_totals': {f: 0 for f in totals}, 'unverified_requests': 0, 'requests': 0,
                           'cost_usd': 0.0 if r['cost_usd'] is not None else None}
                    target['daily'].append(day)
                day['requests'] += r['requests']
                day['unverified_requests'] += r['unverified_requests']
                for field in totals:
                    day['totals'][field] += r['totals'][field]
                    day['reported_totals'][field] += r['reported_totals'][field]
                if r['cost_usd'] is not None:
                    day['cost_usd'] += r['cost_usd']
            target.pop('date', None)
            target['requests'] += r['requests']
            target['unverified_requests'] += r['unverified_requests']
            for field in totals:
                target['totals'][field] += r['totals'][field]
                target['reported_totals'][field] += r['reported_totals'][field]
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
                    'source': {'tokens': 0 if status == 'not_configured' else None, 'complete': status == 'not_configured', 'status': status}}
        key = os.environ.get('NORDROUTER_API_KEY', '')
        pricing, stale = (price_client or nordrouter_usage.Client(key)).get('pricing') if key or price_client else (None, True)
        return aggregate(body, pricing, start, end, status=status, fetched_at=fetched_at,
                         pricing_stale=stale, include_daily=include_daily)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {'models': [], 'daily': [], 'status': 'unavailable',
                'source': {'tokens': None, 'complete': False, 'status': 'unavailable'}}


def match_account_model(serving, account_ids):
    """Exact account id, or one unambiguous catalog tail. Never strip route suffixes."""
    serving = str(serving or '')
    if serving in account_ids:
        return serving
    tails = [item for item in account_ids if item.rsplit('/', 1)[-1] == serving]
    return tails[0] if len(tails) == 1 else None


def _token_total(totals):
    value = (totals or {}).get('total')
    return value if type(value) is int and value >= 0 else None


def _window_tokens(route, nr_dates):
    """Tokens that can overlap this account window. Outside dates are not deducted."""
    total = _token_total(route.get('reported_totals', route.get('totals') if route.get('usage_provenance') not in ('estimated', 'mixed') else None))
    if not total:
        return 0
    days = route.get('daily') or []
    if nr_dates is None:
        return total
    if not days:
        return 0
    return sum(_token_total(day.get('reported_totals', day.get('totals'))) or 0 for day in days if day.get('date') in nr_dates)


def account_overlap(models, *, router_status=None, nr_daily=None):
    """Residual of NordRouter account models after proven bucket overlap.

    Account billed tokens and dollars stay on the row. ``exclusive_tokens`` is
    only the part not proven to be inside a NordRouter-origin serving route.
    A larger router observation cannot make the residual negative: the excess
    is ``overlap_unreconciled_tokens`` and remains on the bucket. Routes whose
    origin or id does not match an account model are not deducted. Suffix
    normalisation is not a match. Missing router data is not treated as zero
    overlap. Day residuals are omitted unless every moved route has daily
    totals that sum to its window total; no proportional day split is invented.
    """
    account_rows = [row for row in models if row.get('source') == 'nordrouter']
    account_ids = [str(row.get('model') or '') for row in account_rows]
    router_rows = [row for row in models if row.get('source') == 'omniroute' and row.get('bucket_usage')]
    nr_dates = None if nr_daily is None else {day.get('date') for day in nr_daily}
    has_router = bool(router_rows)
    retention_limited = any(row['bucket_usage'].get('retention_limited') for row in router_rows)
    proven = defaultdict(int)
    matched_buckets = defaultdict(set)
    unknown = []
    movable = []
    for row in router_rows:
        for route in row['bucket_usage'].get('routes') or []:
            tokens = _window_tokens(route, nr_dates)
            if not tokens:
                continue
            origin = route.get('nordrouter')
            if origin is True:
                match = match_account_model(route.get('model'), account_ids)
                if match is None:
                    unknown.append({'bucket': row.get('model'), 'model': route.get('model'),
                                    'tokens': tokens, 'reason': 'unmatched'})
                    continue
                proven[match] += tokens
                matched_buckets[match].add(row.get('model'))
                movable.append((route, row, 'matched'))
            elif origin is False:
                movable.append((route, row, 'other'))
            else:
                unknown.append({'bucket': row.get('model'), 'model': route.get('model'),
                                'tokens': tokens, 'reason': 'no_route_map'})
    seen = set()
    annotations = []
    unmapped = any(item['reason'] == 'no_route_map' for item in unknown)
    for row in account_rows:
        model = str(row.get('model') or '')
        account = _token_total(row.get('totals'))
        billed = row.get('cost_usd') if isinstance(row.get('cost_usd'), (int, float)) and type(row.get('cost_usd')) is not bool else None
        note = {'account_billed_tokens': account, 'account_billed_cost_usd': billed}
        if account is None:
            note.update(exclusive_status='unavailable', exclusive_tokens=None,
                        overlap_proven_tokens=0, overlap_unreconciled_tokens=0, overlap_unknown_tokens=0)
        elif not has_router:
            clean = router_status in ('ok', 'stale')
            note.update(exclusive_status='partitioned' if clean else 'unavailable',
                        exclusive_tokens=account if clean else None,
                        overlap_proven_tokens=0, overlap_unreconciled_tokens=0, overlap_unknown_tokens=0)
        elif model in seen:
            note.update(exclusive_status='duplicate', exclusive_tokens=0,
                        overlap_proven_tokens=0, overlap_unreconciled_tokens=0, overlap_unknown_tokens=0)
        elif not proven and unmapped:
            note.update(exclusive_status='no_route_map', exclusive_tokens=None,
                        overlap_proven_tokens=0, overlap_unreconciled_tokens=0, overlap_unknown_tokens=0)
        else:
            seen.add(model)
            matched = proven.get(model, 0)
            deducted = min(account, matched)
            unknown_tokens = sum(item['tokens'] for item in unknown
                                 if item['reason'] == 'unmatched' and item['bucket'] in matched_buckets.get(model, ()))
            note.update(exclusive_status='partial_retention' if retention_limited else 'partitioned',
                        exclusive_tokens=account - deducted, overlap_proven_tokens=deducted,
                        overlap_unreconciled_tokens=max(0, matched - account),
                        overlap_unknown_tokens=unknown_tokens)
            seen.add(model)
        annotations.append(note)
    days_ready = False
    daily = None
    if has_router and movable:
        days_ready = True
        matched_by_date = defaultdict(int)
        for route, _row, kind in movable:
            days = route.get('daily') or []
            covered = sum(_token_total(day.get('reported_totals', day.get('totals'))) or 0 for day in days)
            if not days or (nr_dates is None and covered != _token_total(route.get('totals'))):
                days_ready = False
                break
            if kind == 'matched':
                for day in days:
                    if nr_dates is not None and day.get('date') not in nr_dates:
                        continue
                    value = _token_total(day.get('reported_totals', day.get('totals'))) or 0
                    if value:
                        matched_by_date[day['date']] += value
        if days_ready:
            daily = []
            for day in nr_daily or []:
                account = day.get('tokens')
                if type(account) is not int or account < 0:
                    days_ready = False
                    daily = None
                    break
                matched = matched_by_date.get(day['date'], 0)
                subtracted = min(account, matched)
                daily.append({'date': day['date'], 'tokens': account - subtracted,
                              'subtracted': subtracted, 'unreconciled': max(0, matched - account)})
    proven_tokens = sum(note.get('overlap_proven_tokens') or 0 for note in annotations)
    # A day-wide cap and per-model window caps are not interchangeable. Without
    # account model-day data, refuse a daily partition that contradicts the
    # model-window reconciliation instead of publishing two different residuals.
    if daily is not None and sum(day['subtracted'] for day in daily) != proven_tokens:
        daily = None
        days_ready = False
    unreconciled_tokens = sum(note.get('overlap_unreconciled_tokens') or 0 for note in annotations)
    statuses = {note.get('exclusive_status') for note in annotations}
    if not account_rows:
        status = 'empty'
    elif statuses <= {'partitioned', 'duplicate'} and not retention_limited:
        status = 'partitioned'
    elif 'partial_retention' in statuses:
        status = 'partial_retention'
    elif statuses <= {'no_route_map'}:
        status = 'no_route_map'
    elif 'unavailable' in statuses and 'partitioned' not in statuses and 'partial_retention' not in statuses:
        status = 'unavailable'
    else:
        status = 'partial'
    exclusive_values = [note.get('exclusive_tokens') for note in annotations]
    return {'annotations': annotations, 'status': status, 'has_router': has_router,
            'retention_limited': retention_limited, 'unknown': unknown, 'days_ready': days_ready,
            'daily': daily, 'proven_tokens': proven_tokens, 'unreconciled_tokens': unreconciled_tokens,
            'unknown_tokens': sum(item['tokens'] for item in unknown),
            'exclusive_tokens': None if any(value is None for value in exclusive_values) else sum(exclusive_values)}
