"""Snapshot-only OpenClaw projection; legacy saved-window codecs for offline imports.

No default code path can invoke OpenClaw. Explicit injected fetches are retained
only for offline compatibility with old saved-window fixtures.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import subprocess

import dashboard_store

ROOT = Path(__file__).resolve().parent
DB_NAME = 'dashboard.sqlite'
# Snapshots written before the database existed; read once, then left in place.
LEGACY_DIR = ROOT / 'data' / 'openclaw'
CACHE_DIR = LEGACY_DIR
# A saved window is disposable; bound how much stale history can accumulate.
CACHE_RETENTION_SECONDS = 45 * 24 * 60 * 60
CACHE_WINDOW_LIMIT = 24
_TOTAL_FIELDS = ('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens',
                 'totalCost', 'missingCostEntries')
_SNAPSHOT_NAME = re.compile(
    r'snapshot-days_\d+_start_\d{4}-\d{2}-\d{2}_end_\d{4}-\d{2}-\d{2}\.json\Z')
# Anything long and opaque could be a credential echoed by a failure.
_OPAQUE = re.compile(r'[A-Za-z0-9_\-]{24,}')


def fetch_usage(*args, **kwargs):
    raise RuntimeError('Live OpenClaw transport removed; use the scheduled committed collector')


def db_file(cache_dir=None) -> Path:
    """The dashboard-owned database that holds saved windows.

    ``cache_dir`` names the directory holding the database, and a ``*.sqlite``
    path is taken as the file itself. The legacy ``OPENCLAW_USAGE_CACHE_DIR``
    variable keeps working for a configured location, while
    ``AI_USAGE_DASHBOARD_DB`` sets the file explicitly.
    """
    explicit = cache_dir if cache_dir is not None \
        else os.environ.get('OPENCLAW_USAGE_CACHE_DIR')
    if explicit:
        path = Path(explicit)
        return path if path.suffix == '.sqlite' else path / DB_NAME
    return dashboard_store.db_path()


def cache_root(cache_dir=None) -> Path:
    """Directory holding the dashboard database; kept for callers that need it."""
    return db_file(cache_dir).parent


def _window_key(days, start_date, end_date) -> str:
    """Key one saved window by its exact period; an unknown period is refused."""
    return dashboard_store.window_key(days, start_date, end_date)


def _sanitize_aggregate(response: dict) -> dict:
    """Keep only the aggregate fields this module projects.

    The Gateway response is aggregate-only, but it is narrowed here as well so
    the retained snapshot can never hold a field a later Gateway version adds.
    Live and cached reads project from this same shape, so both paths compute
    identical tokens, costs, and daily history.
    """
    aggregates = response.get('aggregates') if isinstance(response, dict) else None
    if (not isinstance(aggregates, dict) or
            not all(isinstance(aggregates.get(key), list) for key in ('byModel', 'modelDaily'))):
        raise ValueError('OpenClaw usage aggregate unavailable')
    by_model, model_daily = [], []
    for row in aggregates.get('byModel') or []:
        if not isinstance(row, dict):
            raise ValueError('Malformed Gateway aggregate row')
        provider, model = row.get('provider'), row.get('model')
        if not isinstance(provider, str) or not isinstance(model, str):
            raise ValueError('Malformed Gateway model identity')
        totals = row.get('totals')
        if not isinstance(totals, dict) or type(totals.get('totalTokens')) is not int or totals['totalTokens'] < 0:
            raise ValueError('Malformed Gateway model totals')
        for field in ('input', 'output', 'cacheRead', 'cacheWrite'):
            if field in totals and (type(totals[field]) is not int or totals[field] < 0):
                raise ValueError('Malformed Gateway token category')
        by_model.append({'provider': provider, 'model': model, 'totals': {
            key: value for key, value in totals.items()
            if key in _TOTAL_FIELDS and isinstance(value, (int, float))
            and not isinstance(value, bool)}})
    for row in aggregates.get('modelDaily') or []:
        if not isinstance(row, dict):
            raise ValueError('Malformed Gateway aggregate row')
        provider, model = row.get('provider'), row.get('model')
        if not isinstance(provider, str) or not isinstance(model, str):
            raise ValueError('Malformed Gateway model identity')
        if (not isinstance(row.get('date'), str) or type(row.get('tokens')) is not int
                or row['tokens'] < 0):
            raise ValueError('Malformed Gateway daily usage')
        datetime.strptime(row['date'], '%Y-%m-%d')
        entry = {'date': row['date'], 'provider': provider, 'model': model,
                 'tokens': row['tokens']}
        if isinstance(row.get('cost'), (int, float)) and not isinstance(row['cost'], bool):
            entry['cost'] = row['cost']
        model_daily.append(entry)
    data = {'aggregates': {'byModel': by_model, 'modelDaily': model_daily}}
    for key in ('startDate', 'endDate'):
        if isinstance(response.get(key), str):
            data[key] = response[key]
    if type(response.get('updatedAt')) is int:
        data['updatedAt'] = response['updatedAt']
    reported = response.get('cacheStatus')
    if isinstance(reported, dict):
        data['cacheStatus'] = {k: v for k, v in reported.items()
            if (k == 'status' and v in ('fresh', 'refreshing', 'partial', 'stale'))
            or (k in ('cachedFiles', 'pendingFiles', 'staleFiles', 'refreshedAt')
                and type(v) is int and v >= 0)}
        if isinstance(reported.get('partial'), dict):
            data['cacheStatus']['partial'] = {'reason': str(reported['partial'].get('reason', 'unknown'))[:80]}
    if isinstance(response.get('partial'), dict):
        data['partial'] = {'reason': str(response['partial'].get('reason', 'unknown'))[:80]}
    coverage = response.get('coverage')
    if isinstance(coverage, dict):
        # The transport emits aggregate provenance only. Validate/bound every
        # nested object too, so a future SDK cannot retain a session or secret.
        clean = {'complete': coverage.get('complete') is True}
        for key in ('strategy', 'observedFromUtc', 'observedUntilUtc'):
            if isinstance(coverage.get(key), str):
                clean[key] = coverage[key][:100]
        if isinstance(coverage.get('requests'), list):
            clean['requests'] = []
            for row in coverage['requests'][:2]:
                if not isinstance(row, dict):
                    continue
                clean['requests'].append({k: v for k, v in row.items() if
                    (k in ('startDate', 'endDate', 'status', 'reason') and isinstance(v, str) and len(v) <= 100)
                    or (k == 'complete' and type(v) is bool)
                    or (k == 'milliseconds' and type(v) is int and v >= 0)})
        data['coverage'] = clean
    return data


def _validate_window(data, start_date, end_date):
    if any(k in data and data[k] != v for k, v in
           (('startDate', start_date), ('endDate', end_date))):
        raise ValueError('Gateway returned a different UTC window')
    if any(not start_date <= row['date'] <= end_date for row in data['aggregates']['modelDaily']):
        raise ValueError('Gateway model day outside requested UTC window')


def snapshot_complete(data) -> bool:
    """Coverage, not JSON validity or nonzero tokens, certifies a full snapshot."""
    status = data.get('cacheStatus') or {}
    if (status.get('status') != 'fresh' or status.get('pendingFiles', 0) or
            status.get('staleFiles', 0) or status.get('partial') or data.get('partial') or
            data.get('coverage', {}).get('complete') is False):
        return False
    models, days = {}, {}
    for row in data['aggregates']['byModel']:
        key = (row['provider'], row['model'])
        if key in models:
            return False
        models[key] = row['totals']['totalTokens']
    identities = set()
    for row in data['aggregates']['modelDaily']:
        key = (row['provider'], row['model'])
        identity = (*key, row['date'])
        if key not in models or identity in identities:
            return False
        identities.add(identity)
        days[key] = days.get(key, 0) + row['tokens']
    return all(tokens == days.get(key, 0) for key, tokens in models.items())


def _sanitize_error(exc: BaseException) -> str:
    """A bounded, non-secret failure summary for the dashboard banner.

    A subprocess failure is summarized by what happened, because its default
    message is a long command line whose useful tail would be truncated away.
    """
    name = type(exc).__name__
    if isinstance(exc, subprocess.TimeoutExpired):
        message = f'gateway command exceeded {exc.timeout:g}s'
    elif isinstance(exc, subprocess.CalledProcessError):
        message = f'gateway command failed with exit status {exc.returncode}'
    else:
        message = str(exc).strip()
    text = f'{name}: {message}' if message else name
    text = text.replace(str(Path.home()), '~')
    text = _OPAQUE.sub('[redacted]', text)
    return ' '.join(text.split())[:200]


def _validate_record(record, days, start_date, end_date) -> dict | None:
    """A saved window is usable only when it describes exactly the requested period."""
    if not isinstance(record, dict):
        return None
    if (record.get('days') != days or record.get('start_date') != start_date
            or record.get('end_date') != end_date):
        return None
    fetched_at = record.get('fetched_at_utc')
    if not isinstance(fetched_at, str):
        return None
    try:
        stamp = datetime.fromisoformat(fetched_at)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        return None
    payload = record.get('payload')
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    try:
        data = _sanitize_aggregate(data)
        _validate_window(data, start_date, end_date)
    except (ValueError, TypeError, AttributeError, KeyError):
        return None
    return {'fetched_at_utc': stamp.astimezone(timezone.utc).isoformat(timespec='seconds'),
            'data': data}


_legacy_scanned: set[str] = set()


def _legacy_dirs(cache_dir=None) -> list[Path]:
    """Directories that may hold snapshots from before the database existed."""
    dirs = [LEGACY_DIR, db_file(cache_dir).parent]
    unique: list[Path] = []
    for directory in dirs:
        if directory not in unique:
            unique.append(directory)
    return unique


def _import_legacy(cache_dir=None) -> None:
    """Copy pre-database snapshots into the table, at most once per process.

    Legacy files are read, never moved or deleted, and any that cannot be
    validated are skipped: a migration must not invent a usable window.
    """
    target = db_file(cache_dir)
    for legacy in _legacy_dirs(cache_dir):
        marker = str(legacy)
        if marker in _legacy_scanned:
            continue
        _legacy_scanned.add(marker)
        try:
            files = sorted(legacy.glob('snapshot-*.json'))
        except OSError:
            continue
        # Import only real legacy data: a read must not create a database for
        # a directory that never held saved windows.
        if not any(_SNAPSHOT_NAME.match(path.name) for path in files):
            continue
        for path in files:
            if not _SNAPSHOT_NAME.match(path.name):
                continue
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            window = record.get('window') if isinstance(record, dict) else None
            if not isinstance(window, dict):
                continue
            days = window.get('days')
            start_date, end_date = window.get('start_date'), window.get('end_date')
            validated = _validate_record(
                {'days': days, 'start_date': start_date, 'end_date': end_date,
                 'fetched_at_utc': record.get('fetched_at_utc'),
                 'payload': json.dumps(record.get('data'))},
                days, start_date, end_date)
            if validated is None:
                continue
            try:
                key = _window_key(days, start_date, end_date)
                dashboard_store.store_snapshot(
                    target, key, days, start_date, end_date,
                    validated['fetched_at_utc'], json.dumps(validated['data']),
                    retention_seconds=CACHE_RETENTION_SECONDS,
                    window_limit=CACHE_WINDOW_LIMIT, replace=False)
            except (ValueError, OSError, dashboard_store.StoreUnavailable):
                return


def read_snapshot(days, start_date, end_date, *, cache_dir=None) -> dict | None:
    """Return the saved window for exactly this period, or ``None``.

    A missing database, an unusable period, and a foreign or corrupt file are
    all the same answer here: no saved window can be served. Snapshots written
    before the database existed are imported first, which is why a read only
    creates the file when such legacy data is actually present.
    """
    try:
        key = _window_key(days, start_date, end_date)
        target = db_file(cache_dir)
        row = dashboard_store.read_snapshot(target, key)
        if row is None:
            _import_legacy(cache_dir)
            row = dashboard_store.read_snapshot(target, key)
    except (ValueError, OSError, dashboard_store.StoreUnavailable):
        return None
    return _validate_record(row, days, start_date, end_date)


def write_snapshot(days, start_date, end_date, response, *, cache_dir=None,
                   fetched_at=None) -> str:
    """Admit a verified replacement (or first unverified observation)."""
    key = _window_key(days, start_date, end_date)
    data = _sanitize_aggregate(response)
    _validate_window(data, start_date, end_date)
    previous = read_snapshot(days, start_date, end_date, cache_dir=cache_dir)
    if previous is not None and not snapshot_complete(data):
        raise ValueError('Incomplete Gateway read cannot replace a saved window')
    stamp = (fetched_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    written = stamp.isoformat(timespec='seconds')
    target = db_file(cache_dir)
    if dashboard_store.path_unusable(target):
        raise ValueError('Saved-window database must not be a symlink')
    _import_legacy(cache_dir)
    try:
        dashboard_store.store_snapshot(
            target, key, days, start_date, end_date, written, json.dumps(data),
            retention_seconds=CACHE_RETENTION_SECONDS,
            window_limit=CACHE_WINDOW_LIMIT, replace=snapshot_complete(data))
    except dashboard_store.StoreUnavailable as exc:
        raise ValueError(str(exc)) from exc
    return written


def _source_instant(data, fallback):
    if type(data.get('updatedAt')) is int:
        try:
            return datetime.fromtimestamp(data['updatedAt'] / 1000, timezone.utc).isoformat(timespec='seconds')
        except (ValueError, OverflowError, OSError):
            pass
    return fallback


def _saved_result(record, start_date, end_date, *, error=None,
                  upstream_status=None, refreshing=False):
    return {'status': 'cached', 'response': record['data'],
            'cached_at_utc': record['fetched_at_utc'], 'gateway_cache_status': None,
            'upstream_status': upstream_status, 'error': error,
            'snapshot_written_at_utc': None, 'snapshot_complete': snapshot_complete(record['data']),
            'source_at_utc': _source_instant(record['data'], record['fetched_at_utc']), 'refreshing': refreshing,
            'saved_window_start': start_date, 'saved_window_end': end_date, 'window_matches': True}


def load_with_cache(days, start_date, end_date, *, cache_dir=None, fetch=None,
                    background=False) -> dict:
    """Default: owned snapshot only, including former background callers.

    An explicit injected fetch supports offline legacy codec/accounting fixtures.
    It is never selected by HTTP or production collection.
    """
    if fetch is None or background:
        from openclaw_snapshot import load
        return load(days, start_date, end_date)

    try:
        data = _sanitize_aggregate(fetch(start_date, end_date))
        _validate_window(data, start_date, end_date)
        complete = snapshot_complete(data)
        gateway_cache_status = (data.get('cacheStatus') or {}).get('status')
        if not complete:
            gateway_cache_status = gateway_cache_status if gateway_cache_status != 'fresh' else 'partial'
            record = read_snapshot(days, start_date, end_date, cache_dir=cache_dir)
            if record:
                return _saved_result(record, start_date, end_date, upstream_status=gateway_cache_status or 'unknown')
        observed_at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        try:
            written_at = write_snapshot(days, start_date, end_date, data, cache_dir=cache_dir)
        except (OSError, ValueError):
            written_at = None
        if not complete:
            admitted = read_snapshot(days, start_date, end_date, cache_dir=cache_dir)
            if admitted and snapshot_complete(admitted['data']):
                return _saved_result(admitted, start_date, end_date, upstream_status=gateway_cache_status or 'unknown')
        return {'status': 'ok', 'response': data, 'cached_at_utc': None,
                'gateway_cache_status': gateway_cache_status or 'partial', 'error': None,
                'snapshot_written_at_utc': written_at, 'snapshot_complete': complete,
                'source_at_utc': _source_instant(data, observed_at), 'upstream_status': gateway_cache_status or 'unknown',
                'refreshing': False}
    except (OSError, ValueError, TypeError, KeyError, AttributeError,
            RuntimeError, subprocess.SubprocessError) as exc:
        error = _sanitize_error(exc)
    record = read_snapshot(days, start_date, end_date, cache_dir=cache_dir)
    if record is not None:
        return _saved_result(record, start_date, end_date, error=error, upstream_status='unavailable')
    return {'status': 'unavailable', 'response': None, 'cached_at_utc': None,
            'gateway_cache_status': None, 'upstream_status': 'unavailable', 'error': error,
            'snapshot_written_at_utc': None, 'snapshot_complete': False,
            'source_at_utc': None, 'refreshing': False}


def _tokens(row: dict) -> dict:
    values = row.get('totals') or {}
    return {
        'input': values.get('input'), 'output': values.get('output'),
        'cache_read': values.get('cacheRead'),
        'cache_write': values.get('cacheWrite'),
        'total': values.get('totalTokens'),
    }


def model_entries(response: dict, *, include_daily: bool) -> list[dict]:
    """Project provider/model identity, including comparison-only NordRouter rows.

    Model names alone do not prove a route; use the reported provider field.
    Gateway aggregates do not identify provider accounts.
    """
    aggregate = response['aggregates']
    days: dict[tuple[str, str], list[dict]] = {}
    for row in aggregate.get('modelDaily', []):
        provider, model = row.get('provider'), row.get('model')
        if not isinstance(provider, str) or not isinstance(model, str):
            continue
        if not isinstance(row.get('date'), str) or not isinstance(row.get('tokens'), int):
            continue
        days.setdefault((provider, model), []).append({
            'date': row['date'], 'input': row.get('input'), 'output': row.get('output'),
            'cache_read': row.get('cacheRead'), 'cache_write': row.get('cacheWrite'), 'total': row['tokens'],
            'cost_usd': row.get('cost') if isinstance(row.get('cost'), (int, float)) else None,
        })
    entries = []
    for row in aggregate.get('byModel', []):
        provider, model = row.get('provider'), row.get('model')
        if not isinstance(provider, str) or not isinstance(model, str):
            continue
        totals = _tokens(row)
        if not isinstance(totals['total'], int) or totals['total'] <= 0:
            continue
        daily = sorted(days.get((provider, model), []), key=lambda d: d['date'])
        raw_totals = row.get('totals') or {}
        modeled_cost = raw_totals.get('totalCost')
        missing_cost = (raw_totals.get('missingCostEntries') or 0) > 0
        cost_kind = None
        if not isinstance(modeled_cost, (int, float)) or (missing_cost and modeled_cost <= 0):
            modeled_cost = None
        else:
            cost_kind = 'partial' if missing_cost else 'reported'
        entries.append({
            'source': 'openclaw', 'provider': provider, 'model': model,
            'totals': totals,
            'daily': daily if include_daily else [],
            # Gateway cost is a model-price estimate, not a provider invoice.
            'cost_usd': modeled_cost,
            'cost_kind': cost_kind,
        })
    return entries
