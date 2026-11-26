"""Opt-in OpenCodex aggregates: replacements, never token deltas or billing proof.

No transport, raw logs, account metadata or price inference.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3

import project_config

UPSTREAM_SHA = '667662903a6c139524506667d12656d9ad50fad0'  # v2.99.0
SOURCE_LABEL = 'OpenCodex на Macbook'
MAX_BYTES = 8 * 1024 * 1024
MAX_DAYS = 30
APP_ID = 0x4F435855
VERSION = 2
COUNTERS = {'totalTokens': 'total_tokens', 'inputTokens': 'input_tokens',
            'outputTokens': 'output_tokens', 'cacheReadInputTokens': 'cache_read_tokens',
            'cacheCreationInputTokens': 'cache_creation_tokens',
            'cachedInputTokens': 'cached_input_tokens', 'reasoningOutputTokens': 'reasoning_output_tokens'}
LIMITATIONS = ('Saved OpenCodex observations, not additional overview usage. '
               'May overlap Codex, OpenClaw, Hermes and router observations. '
               'Provider/model labels do not establish billing or serving identity. '
               'Account metadata is not exported; accounts remain unknown. '
               'Source-reported API list-price estimate is not spend or billing. '
               'Measurement coverage includes upstream-estimated usage; raw reported/estimated/unknown counts remain separate. '
               'Retained ledger only; deleted history cannot be reconstructed. '
               'Current day is observed only through the recorded source time.')


class InvalidSnapshot(ValueError):
    """An incomplete, ambiguous or unsupported observation was refused."""


def enabled():
    project_config.load_env()
    return os.environ.get('OPENCODEX_USAGE_ENABLED') == '1'


def db_path(path=None):
    return Path(path or os.environ.get('OPENCODEX_USAGE_DB') or
                Path(__file__).resolve().parent / 'data/opencodex.sqlite')


def source_id(value=None):
    value = value or os.environ.get('OPENCODEX_USAGE_SOURCE_ID', 'private-mac')
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', value):
        raise InvalidSnapshot('Invalid opaque source identity')
    return value


def _counter(value):
    if type(value) is not int or not 0 <= value <= 2**53 - 1:
        raise InvalidSnapshot('Invalid aggregate counter')
    return value


def _label(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 256 or
            any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise InvalidSnapshot('Invalid aggregate label')
    return value  # Never strip provider prefixes or merge aliases.


def bounds(day):
    parsed = date.fromisoformat(day)
    if parsed.isoformat() != day:
        raise InvalidSnapshot('Expected an ISO UTC date')
    start = int(datetime.combine(parsed, datetime.min.time(), timezone.utc).timestamp() * 1000)
    # Upstream custom windows use INCLUSIVE epoch milliseconds.
    return start, start + 86400000 - 1


def dates(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    count = (last - first).days + 1
    if not 1 <= count <= MAX_DAYS:
        raise InvalidSnapshot('Expected 1 through 30 UTC days')
    return [(first + timedelta(days=n)).isoformat() for n in range(count)]


def _tokens(row):
    return {target: (_counter(row[key]) if key in row else None)
            for key, target in COUNTERS.items()}


SUMMARY_COUNTS = ('requests', 'attemptCount', 'measuredRequests', 'reportedRequests',
                  'unreportedRequests', 'unsupportedRequests', 'estimatedRequests',
                  'pricedRequests', 'unpricedRequests', 'unmeteredRequests',
                  'sends', 'settledSends', 'unresolvedSends', 'spendRequests',
                  'cacheObservedInputTokens', 'cacheObservedRequests',
                  'cacheSynthesizedRequests', 'cacheUnknownRequests')
MODEL_COUNTS = ('requests', 'attemptCount', 'measuredRequests', 'reportedRequests',
                'estimatedRequests', 'pricedRequests', 'unpricedRequests', 'cacheObservedInputTokens')
SUMMARY_FIELDS = set(SUMMARY_COUNTS) | set(COUNTERS) | {'coverageRatio', 'estimatedCostUsd'}
MODEL_FIELDS = set(MODEL_COUNTS) | set(COUNTERS) | {'provider', 'model', 'resolvedModel',
               'hasUnresolvedRequestedModel', 'estimatedCostUsd', 'priceCoverageRatio'}
WINDOW_FIELDS = {'range', 'surface', 'customWindow', 'since', 'until', 'generatedAt',
                 'historyTruncated', 'entriesTruncated', 'entriesDropped', 'truncatedPrefixBytes',
                 'summary', 'models'}


def _number(value, *, ratio=False):
    if (type(value) not in (int, float) or not math.isfinite(value) or value < 0 or
            (ratio and value > 1)):
        raise InvalidSnapshot('Invalid aggregate estimate or ratio')
    return value


def _raw_counters(row, keys):
    return {key: _counter(row[key]) for key in (*keys, *COUNTERS) if key in row}


def project_window(body):
    """Mac-local allowlist. Never export accounts, keys, apps, filters or logs."""
    if not isinstance(body, dict):
        raise InvalidSnapshot('Expected CLI JSON object')
    if (body.get('usageIncomplete', False) is not False or body.get('error') or
            body.get('warnings') or body.get('sourceFilter') is not None or
            body.get('source') is not None or body.get('scope') is not None or
            body.get('modelView') is not None):
        raise InvalidSnapshot('Incomplete or scoped CLI observation')
    echo = body.get('filter', {})
    if not isinstance(echo, dict) or any(echo.get(k) is not None for k in ('provider', 'model', 'apiKeyId')):
        raise InvalidSnapshot('Unexpected attribution filter')
    result = {k: v for k, v in body.items() if k in WINDOW_FIELDS}
    if not isinstance(body.get('summary'), dict) or not isinstance(body.get('models'), list):
        raise InvalidSnapshot('Missing aggregate breakdown')
    result['summary'] = {k: v for k, v in body['summary'].items() if k in SUMMARY_FIELDS}
    result['models'] = [{k: v for k, v in row.items() if k in MODEL_FIELDS}
                        for row in body['models'] if isinstance(row, dict)]
    if len(result['models']) != len(body['models']):
        raise InvalidSnapshot('Invalid model aggregate')
    return result


def normalize_day(day, body):
    """Closed projection of /api/usage custom UTC windows, not local-day buckets."""
    start, end = bounds(day)
    if not isinstance(body, dict):
        raise InvalidSnapshot('Expected a usage summary object')
    if (body.get('range') != 'all' or body.get('surface') != 'all' or
            body.get('customWindow') is not True or
            type(body.get('since')) is not int or body['since'] != start or
            type(body.get('until')) is not int or body['until'] != end):
        raise InvalidSnapshot('Source did not echo the exact all-surface UTC window')
    for flag in ('historyTruncated', 'entriesTruncated'):
        if body.get(flag) is not False:
            raise InvalidSnapshot('Incomplete retained history')
    for key in ('entriesDropped', 'truncatedPrefixBytes'):
        if _counter(body.get(key)) != 0:
            raise InvalidSnapshot('Incomplete retained history')
    if (body.get('usageIncomplete', False) is not False or body.get('error') or
            body.get('warnings') or body.get('sourceFilter') is not None):
        raise InvalidSnapshot('Partial or unexpected filtered observation')
    filter_echo = body.get('filter', {})
    if not isinstance(filter_echo, dict) or any(
            filter_echo.get(k) is not None for k in ('provider', 'model', 'apiKeyId')):
        raise InvalidSnapshot('Unexpected attribution filter')
    generated = _counter(body.get('generatedAt'))
    if generated < start or generated > int(datetime.now(timezone.utc).timestamp() * 1000) + 60000:
        raise InvalidSnapshot('Observation has an invalid source time')
    summary = body.get('summary')
    if not isinstance(summary, dict):
        raise InvalidSnapshot('Missing summary coverage')
    requests = _counter(summary.get('requests'))
    counts = {k: _counter(summary.get(k)) for k in
              ('reportedRequests', 'estimatedRequests', 'measuredRequests',
               'unreportedRequests', 'unsupportedRequests')}
    if (counts['reportedRequests'] + counts['estimatedRequests'] != counts['measuredRequests'] or
            counts['measuredRequests'] + counts['unreportedRequests'] + counts['unsupportedRequests'] != requests):
        raise InvalidSnapshot('Invalid measurement coverage')
    coverage = _number(summary.get('coverageRatio'), ratio=True)
    if not math.isclose(coverage, counts['measuredRequests'] / requests if requests else 0, abs_tol=1e-9):
        raise InvalidSnapshot('Coverage ratio does not reconcile')
    total = _counter(summary.get('totalTokens'))
    raw_models = body.get('models')
    if not isinstance(raw_models, list) or len(raw_models) > 256:
        raise InvalidSnapshot('Missing or oversized model breakdown')
    models, seen = [], set()
    for row in raw_models:
        if not isinstance(row, dict):
            raise InvalidSnapshot('Invalid model aggregate')
        provider, model = _label(row.get('provider')), _label(row.get('model'))
        identity = provider, model
        if identity in seen or identity == ('other', 'other'):
            raise InvalidSnapshot('Duplicate identity or collapsed model tail')
        seen.add(identity)
        if any(k not in row for k in ('totalTokens', 'inputTokens', 'outputTokens')):
            raise InvalidSnapshot('Missing model token measurements')
        unresolved = row.get('hasUnresolvedRequestedModel', False)
        if type(unresolved) is not bool:
            raise InvalidSnapshot('Invalid model identity provenance')
        model_counts = _raw_counters(row, MODEL_COUNTS)
        measured = _counter(row.get('measuredRequests'))
        if any(_counter(row.get(k)) > _counter(row.get('requests')) for k in
               ('reportedRequests', 'estimatedRequests', 'measuredRequests')):
            raise InvalidSnapshot('Invalid model coverage')
        tokens = _tokens(row)
        raw_total = tokens['total_tokens']
        # Upstream zero-initializes even unmetered rows: expose raw values separately.
        if measured == 0 and row.get('requests'):
            tokens = {key: None for key in tokens}
        models.append(dict(provider=provider, model=model, account=None,
                           identity_kind='upstream_label_not_billing_proof',
                           unresolved_requested_model=unresolved,
                           resolved_model=_label(row['resolvedModel']) if row.get('resolvedModel') else None,
                           requests=_counter(row.get('requests')), measured_requests=measured,
                           reported_counters=model_counts, raw_total_tokens=raw_total,
                           reported_estimated_cost_usd=(_number(row['estimatedCostUsd']) if 'estimatedCostUsd' in row else None),
                           price_coverage_ratio=(_number(row['priceCoverageRatio'], ratio=True) if 'priceCoverageRatio' in row else None),
                           estimated_cost_usd=(_number(row['estimatedCostUsd']) if 'estimatedCostUsd' in row and
                                               (not row.get('requests') or row.get('pricedRequests', 0) > 0) else None),
                           **tokens))
    if sum(m['raw_total_tokens'] for m in models) != total:
        raise InvalidSnapshot('Model totals do not reconcile with the window')
    return dict(date=day, upstream_sha=UPSTREAM_SHA, generated_at_ms=generated, requests=requests,
                total_tokens=total if counts['measuredRequests'] or not requests else None,
                reported_counters=_raw_counters(summary, SUMMARY_COUNTS),
                measurement_coverage=coverage, measurement_complete=counts['reportedRequests'] == requests,
                reported_estimated_cost_usd=(_number(summary['estimatedCostUsd']) if 'estimatedCostUsd' in summary else None),
                estimated_cost_usd=(_number(summary['estimatedCostUsd']) if 'estimatedCostUsd' in summary and
                                    (not requests or summary.get('pricedRequests', 0) > 0) else None),
                estimate_kind='source_reported_api_list_price_not_spend',
                models=sorted(models, key=lambda m: (m['provider'], m['model'])), accounts=[])


def normalize_export(body, expected_source=None):
    try:
        if (not isinstance(body, dict) or type(body.get('schema')) is not int or
                body['schema'] not in (1, 2) or body.get('upstream_sha') != UPSTREAM_SHA):
            raise InvalidSnapshot('Unsupported aggregate export contract or source pin')
        identity = source_id(body.get('source_id'))
        if not body.get('source_id') or (expected_source and identity != source_id(expected_source)):
            raise InvalidSnapshot('Wrong aggregate source identity')
        expected = dates(body['start_date'], body['end_date'])
        windows = body['windows']
        if not isinstance(windows, list) or len(windows) != len(expected):
            raise InvalidSnapshot('Incomplete snapshot batch')
        if body['schema'] == 2:
            if set(body) != {'schema', 'upstream_sha', 'source_id', 'start_date', 'end_date', 'windows'}:
                raise InvalidSnapshot('Unexpected export metadata')
            for w in windows:
                r = w['response']
                if (set(w) != {'date', 'response'} or set(r) - WINDOW_FIELDS or
                        set(r.get('summary', {})) - SUMMARY_FIELDS or
                        any(set(m) - MODEL_FIELDS for m in r.get('models', []))):
                    raise InvalidSnapshot('Non-allowlisted export fields')
        rows = [normalize_day(w['date'], project_window(w['response'])) for w in windows]
        if sorted(r['date'] for r in rows) != expected:
            raise InvalidSnapshot('Duplicate or missing snapshot day')
        return identity, rows
    except (KeyError, TypeError, AttributeError, OverflowError) as exc:
        raise InvalidSnapshot('Malformed aggregate export') from exc


def decode_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InvalidSnapshot('Duplicate JSON field')
            result[key] = value
        return result
    def nonfinite(value):
        raise InvalidSnapshot('Nonfinite JSON number')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def read_export(path):
    with Path(path).open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InvalidSnapshot('Aggregate export exceeds 8 MiB')
    return decode_json(raw)


@contextmanager
def _connection(path, *, write):
    target = db_path(path).absolute()
    if any(p.is_symlink() for p in (target, *target.parents)):
        raise InvalidSnapshot('Symlinked snapshot paths are refused')
    new = False
    if write:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            new = True
        except FileExistsError:
            pass
    elif not target.exists():
        yield None
        return
    conn = sqlite3.connect(target.as_uri() + ('?mode=rw' if write else '?mode=ro'),
                           uri=True, timeout=1, isolation_level=None)
    try:
        conn.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        app = conn.execute('PRAGMA application_id').fetchone()[0]
        version = conn.execute('PRAGMA user_version').fetchone()[0]
        if new:
            if app or version or conn.execute('SELECT name FROM sqlite_master').fetchone():
                raise InvalidSnapshot('Snapshot initialization conflict')
            conn.execute(f'PRAGMA application_id={APP_ID}')
            conn.execute(f'PRAGMA user_version={VERSION}')
            conn.execute('CREATE TABLE snapshots ('
                         'source_id TEXT NOT NULL, day TEXT NOT NULL, generated_ms INTEGER NOT NULL, '
                         'collected_at TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, '
                         'PRIMARY KEY (source_id, day))')
        elif app != APP_ID or version != VERSION:
            raise InvalidSnapshot('Foreign or unsupported snapshot database')
        yield conn
        conn.execute('COMMIT')
    except BaseException:
        if conn.in_transaction:
            conn.execute('ROLLBACK')
        raise
    finally:
        conn.close()


def import_snapshot(body, *, path=None, expected_source=None):
    """Admit a whole batch before writing; a failed day preserves ALL old rows."""
    identity, rows = normalize_export(body, expected_source)
    changed = 0
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with _connection(path, write=True) as conn:
        for row in rows:
            payload = json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False)
            digest = hashlib.sha256(payload.encode()).hexdigest()
            old = conn.execute('SELECT generated_ms, digest FROM snapshots WHERE source_id=? AND day=?',
                               (identity, row['date'])).fetchone()
            if old and (row['generated_at_ms'] < old[0] or
                        (row['generated_at_ms'] == old[0] and digest != old[1])):
                raise InvalidSnapshot('Out-of-order or conflicting source snapshot')
            if old and digest == old[1]:
                continue
            conn.execute('INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?) '
                         'ON CONFLICT(source_id, day) DO UPDATE SET '
                         'generated_ms=excluded.generated_ms, collected_at=excluded.collected_at, '
                         'digest=excluded.digest, payload=excluded.payload',
                         (identity, row['date'], row['generated_at_ms'], stamp, digest, payload))
            changed += 1
        # Bounded batches, full retained history: never age-prune.
    return {'source': 'opencodex', 'source_id': identity, 'days_changed': changed,
            'days_received': len(rows)}


def view(days=7, *, path=None, identity=None):
    """Cache-only source, anchored to its latest imported day, not the wall clock."""
    if type(days) is not int or days not in (0, 1, 7, 30):
        raise InvalidSnapshot('Expected all (0), 1, 7 or 30 day view')
    result = dict(source='opencodex', source_label=SOURCE_LABEL, source_id=source_id(identity), additive=False,
                  billed_cost_usd=None, estimated_cost_usd=None, balance_usd=None,
                  status='disabled' if not enabled() else 'unavailable', daily=[], models=[],
                  accounts=[], limitations=LIMITATIONS, upstream_sha=UPSTREAM_SHA)
    if not enabled():
        return result
    try:
        with _connection(path, write=False) as conn:
            if conn is None:
                return result
            latest = conn.execute('SELECT MAX(day) FROM snapshots WHERE source_id=?',
                                  (result['source_id'],)).fetchone()[0]
            if latest is None:
                return result
            start = ((date.fromisoformat(latest) - timedelta(days=days - 1)).isoformat() if days else
                     conn.execute('SELECT MIN(day) FROM snapshots WHERE source_id=?', (result['source_id'],)).fetchone()[0])
            stored = conn.execute('SELECT day, collected_at, payload FROM snapshots '
                                  'WHERE source_id=? AND day BETWEEN ? AND ? ORDER BY day',
                                  (result['source_id'], start, latest)).fetchall()
        by_day = {day: (stamp, json.loads(payload)) for day, stamp, payload in stored}
        models, accounts = {}, []
        count = (date.fromisoformat(latest) - date.fromisoformat(start)).days + 1
        for n in range(count):
            day = (date.fromisoformat(start) + timedelta(days=n)).isoformat()
            item = by_day.get(day)
            if item is None:
                result['daily'].append(dict(date=day, total_tokens=None, models=[], collected_at=None))
                continue
            stamp, row = item
            result['daily'].append({**row, 'collected_at': stamp})
            accounts.extend(dict(date=day, **a) for a in row['accounts'])
            for model in row['models']:
                key = model['provider'], model['model']
                aggregate = models.setdefault(key, dict(provider=key[0], model=key[1], account=None,
                                                       total_tokens=None, known_subtotal_tokens=0,
                                                       measurement_complete=True, estimated_cost_usd=None,
                                                       input_tokens=0, output_tokens=0, cache_read_tokens=0,
                                                       cache_creation_tokens=0))
                if model['total_tokens'] is not None:
                    aggregate['known_subtotal_tokens'] += model['total_tokens']
                    aggregate['total_tokens'] = aggregate['known_subtotal_tokens']
                for category in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens'):
                    if aggregate[category] is None or model.get(category) is None:
                        aggregate[category] = None
                    else:
                        aggregate[category] += model[category]
                aggregate['measurement_complete'] &= row.get('measurement_complete', False)
                if model.get('estimated_cost_usd') is not None:
                    aggregate['estimated_cost_usd'] = (aggregate['estimated_cost_usd'] or 0) + model['estimated_cost_usd']
        estimates = [row.get('estimated_cost_usd') for _, row in by_day.values()]
        measured = sum(row.get('reported_counters', {}).get('measuredRequests', 0) for _, row in by_day.values())
        requests = sum(row['requests'] for _, row in by_day.values())
        coverage_counts = {}
        for raw_key in SUMMARY_COUNTS:
            if all(raw_key in row.get('reported_counters', {}) for _, row in by_day.values()):
                coverage_counts[raw_key] = sum(row['reported_counters'][raw_key] for _, row in by_day.values())
        result.update(reported_counters=coverage_counts)
        result.update(status='saved', start_date=start, end_date=latest, models=list(models.values()),
                      accounts=[], coverage_complete=len(by_day) == count,
                      measurement_coverage=measured / requests if requests else 0,
                      requests=requests, measured_requests=measured,
                      estimated_cost_usd=sum(e for e in estimates if e is not None) if any(e is not None for e in estimates) else None,
                      estimate_kind='source_reported_api_list_price_not_spend',
                      measurement_complete=all(row.get('measurement_complete', False) for _, row in by_day.values()))
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        result.update(status='unavailable', daily=[], models=[], accounts=[])
    return result
