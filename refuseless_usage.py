"""Refuseless-only view of retained OmniRoute telemetry; never account billing."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

import omniroute_usage


class Client(omniroute_usage.Client):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.root = self.root / 'refuseless-v1'

    def _read(self, start, end):
        script = (Path(__file__).parent / 'scripts/refuseless-aggregate.cjs').read_text()
        result = subprocess.run(['docker', 'exec', '-i', self.container, 'node', '-e', script],
                                input=json.dumps({'end': omniroute_usage.bounds(start, end)[1]}),
                                capture_output=True, text=True, check=True, timeout=20)
        body = json.loads(result.stdout)
        if not isinstance(body.get('rows'), list) or body.get('identity') not in ('connection_endpoint', 'unavailable'):
            raise ValueError('Invalid Refuseless aggregate')
        return body


def summarize(body, status, today, fetched_at=None):
    result = {'status': status, 'fetched_at': fetched_at, 'balance_usd': None,
              'billed_cost_usd': None, 'today': None, 'period': None, 'daily': [],
              'additive': False, 'cost_kind': 'estimated',
              'limitations': 'Retained logs only; deleted history unavailable. Current connection endpoint identifies Refuseless; historical endpoint changes cannot be verified. API-equivalent estimates, not Refuseless invoices. Reported tokens only; missing usage stays unknown. Not additive to NordRouter or overview.'}
    if body is None or body.get('identity') != 'connection_endpoint':
        result['status'] = status if body is None else 'identity_unavailable'
        return result
    rows = body['rows']
    start = min((r['date'] for r in rows), default=today)
    data = omniroute_usage.aggregate({**body, 'history': {}}, None, start, today)
    routes = [r for m in data['models'] for r in m['bucket_usage']['routes']]

    def total(items):
        models = {}
        for route, day in items:
            model = route['model'].rsplit('/', 1)[-1]  # display only, never identity
            row = models.setdefault(model, {'id': model, 'tokens': 0, 'amount_usd': 0.0})
            valid = not day['unverified_requests']
            row['tokens'] = (row['tokens'] + day['reported_totals']['total']
                             if valid and row['tokens'] is not None else None)
            row['amount_usd'] = (row['amount_usd'] + day['cost_usd']
                                 if valid and row['amount_usd'] is not None and day['cost_usd'] is not None else None)
        values = list(models.values())
        return {'tokens': sum(r['tokens'] for r in values) if all(r['tokens'] is not None for r in values) else None,
                'amount_usd': sum(r['amount_usd'] for r in values) if all(r['amount_usd'] is not None for r in values) else None,
                'models': values}

    pairs = [(r, d) for r in routes for d in r['daily']]
    result['start_date'], result['end_date'] = start, today
    result['period'] = total(pairs)
    result['today'] = total([(r, d) for r, d in pairs if d['date'] == today])
    result['daily'] = [{'date': day, **total([(r, d) for r, d in pairs if d['date'] == day])}
                       for day in sorted({d['date'] for _, d in pairs})]
    # The shared serving-route adapter skips empty errors. Their absent usage
    # must not turn this provider's spend/token total into a fabricated zero.
    uncertain = {r['date'] for r in rows if r.get('invalid') or
                 r.get('usage_provenance') != 'reported' or
                 r.get('prompt') is None or r.get('output') is None}
    if uncertain:
        result['period'].update(tokens=None, amount_usd=None)
    if today in uncertain:
        result['today'].update(tokens=None, amount_usd=None)
    daily = {d['date']: d for d in result['daily']}
    for day in uncertain:
        daily.setdefault(day, {'date': day, 'models': []}).update(tokens=None, amount_usd=None)
    result['daily'] = [daily[day] for day in sorted(daily)]
    return result


def collect(client=None):
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        body, status, fetched = (client or Client()).get(today)
        return summarize(body, status, today, fetched)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return summarize(None, 'unavailable', today)
