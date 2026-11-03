"""Read Gateway usage aggregates without reading private transcript content.

The Gateway is the authority for OpenClaw session usage. Its modelDaily rows
contain total tokens/cost only; byModel supplies token-type window totals.

A saved observation is retained as one row per requested window in the
dashboard-owned SQLite database ``data/dashboard.sqlite`` (see
``dashboard_store``). When the Gateway is unavailable, slow, or erroring, the
saved window for exactly that period (1d/7d/30d, UTC calendar dates) is served
with an explicit ``cached`` status, its original successful fetch time, and the
failure text. A failure never rewrites a snapshot and never advances its fetch
time.

A partial first observation is labelled unverified; only a complete read may
replace a saved window. Revalidation is bounded, request-driven and single-flight;
there is no scheduler or poller. The Gateway's own database, Hermes ``state.db``,
and the OmniRoute container's ``storage.sqlite`` are neither read nor written here.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time

import dashboard_store

ROOT = Path(__file__).resolve().parent
DB_NAME = 'dashboard.sqlite'
# Snapshots written before the database existed; read once, then left in place.
LEGACY_DIR = ROOT / 'data' / 'openclaw'
CACHE_DIR = LEGACY_DIR
# One attempt is bounded, and the whole candidate lookup shares one deadline:
# five candidates at the per-attempt bound must never become a 375s page wait.
ATTEMPT_TIMEOUT_SECONDS = 30
FETCH_DEADLINE_SECONDS = 60
CLI_TIMEOUT_MS = 30000
# A saved window is disposable; bound how much stale history can accumulate.
CACHE_RETENTION_SECONDS = 45 * 24 * 60 * 60
CACHE_WINDOW_LIMIT = 24
_TOTAL_FIELDS = ('input', 'output', 'cacheRead', 'cacheWrite', 'totalTokens',
                 'totalCost', 'missingCostEntries')
_SNAPSHOT_NAME = re.compile(
    r'snapshot-days_\d+_start_\d{4}-\d{2}-\d{2}_end_\d{4}-\d{2}-\d{2}\.json\Z')
# Anything long and opaque could be a credential echoed by a failure.
_OPAQUE = re.compile(r'[A-Za-z0-9_\-]{24,}')


def _is_executable(path: Path) -> bool:
    try:
        return path.is_file() and os.access(path, os.X_OK)
    except OSError:
        return False


def _version_key(name: str) -> tuple[int, ...]:
    """Semantic order for an installation name: 2026.10.7 sorts above 2026.9.8.

    Lexicographic order does the opposite (``'2026.9' > '2026.10'``), which used
    to select a build that cannot open the current Gateway data.
    """
    numbers = tuple(int(part) for part in re.findall(r'\d+', name))
    return numbers or (0,)


def openclaw_home() -> Path:
    """The runtime's own home, taken from its environment when it is set."""
    configured = os.environ.get('OPENCLAW_STATE_DIR') or os.environ.get('OPENCLAW_HOME')
    if configured and Path(configured).is_absolute():
        return Path(configured)
    return Path.home() / '.openclaw'


def cli_candidates() -> list[str]:
    """Ordered CLI candidates: explicit override, runtime shim, newest install.

    The shim path is derived from the runtime's own home rather than assumed, and
    every derived candidate must actually exist and be executable. A candidate
    still only counts once it has returned a valid aggregate, so a path that is
    merely present is never treated as working.
    """
    candidates: list[str] = []
    override = os.environ.get('OPENCLAW_USAGE_CLI', '').strip()
    if override:
        candidates.append(override)  # an explicit override is honored as given
    home = openclaw_home()
    for derived in (home / 'tmp' / 'agent-cli' / 'openclaw', home / 'bin' / 'openclaw'):
        candidates.append(str(derived))
    installations = Path.home() / '.local/share/openclaw-installations'
    try:
        builds = sorted((path for path in installations.glob('*/bin/openclaw')),
                        key=lambda path: _version_key(path.parent.parent.name),
                        reverse=True)
    except OSError:
        builds = []
    candidates.extend(str(path) for path in builds)
    ordered: list[str] = []
    for candidate in candidates:
        if not candidate or candidate in ordered:
            continue
        if candidate == override or _is_executable(Path(candidate)):
            ordered.append(candidate)
    return ordered


def _cli() -> str:
    """The first CLI candidate, for callers that only need a path."""
    candidates = cli_candidates()
    if not candidates:
        raise RuntimeError('OpenClaw CLI installation unavailable')
    return candidates[0]


_resolved_cli: str | None = None


def release_cli_cache() -> None:
    """Forget the memoized working CLI (used by tests and by an operator)."""
    global _resolved_cli
    _resolved_cli = None


def _sdk_command(cli: str) -> list[str] | None:
    """Resolve the public SDK beside a trusted installed CLI shim, never config/auth.

    A custom/non-shell CLI keeps its original command-line interface. Shell
    content is parsed, not executed or interpolated into another shell.
    """
    try:
        with Path(cli).resolve().open() as handle:
            wrapper = handle.read(8192)
        for line in wrapper.splitlines():
            words = shlex.split(line)
            if not words or words[0] != 'exec':
                continue
            node = Path(words[1])
            entry = next((Path(w) for w in words[2:]
                          if w.startswith('/') and w.endswith('/dist/index.js')), None)
            if entry is None or not node.is_absolute() or not _is_executable(node):
                continue
            sdk = entry.parent / 'plugin-sdk' / 'gateway-runtime.js'
            if sdk.is_file():
                return [str(node), '--no-use-env-proxy',
                        str(ROOT / 'openclaw_gateway_read.mjs'), str(sdk)]
    except (OSError, ValueError, UnicodeError, IndexError):
        pass
    return None


def _run_cli(cli: str, start_date: str, end_date: str, timeout: float) -> dict:
    """One bounded attempt; returns the validated Gateway response."""
    native = _sdk_command(cli)
    if native:
        argv = native + [start_date, end_date]
    else:
        params = {'agentScope': 'all', 'limit': 1, 'mode': 'utc',
                  'startDate': start_date, 'endDate': end_date}
        argv = [cli, 'gateway', 'call', 'sessions.usage', '--json',
                '--params', json.dumps(params), '--timeout', str(CLI_TIMEOUT_MS)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=True)
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or not isinstance(response.get('aggregates'), dict):
        raise RuntimeError('OpenClaw usage aggregate unavailable')
    return response


def fetch_usage(start_date: str, end_date: str) -> dict:
    """Fetch aggregate-only all-agent usage; no session labels or text retained.

    Candidates are tried in order until one returns a valid aggregate, and the
    winner is memoized for this process. The whole lookup shares one deadline: a
    hung or older CLI cannot stretch the page into a multi-minute wait.
    """
    global _resolved_cli
    deadline = time.monotonic() + FETCH_DEADLINE_SECONDS
    candidates = cli_candidates()
    if _resolved_cli:
        candidates = [_resolved_cli] + [c for c in candidates if c != _resolved_cli]
    if not candidates:
        raise RuntimeError('OpenClaw CLI installation unavailable')
    failures: list[str] = []
    for cli in candidates:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            response = _run_cli(cli, start_date, end_date,
                                min(FETCH_DEADLINE_SECONDS if _sdk_command(cli) else ATTEMPT_TIMEOUT_SECONDS, remaining))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            failures.append(_sanitize_error(exc))
            print(f'OpenClaw usage: candidate failed: {failures[-1]}')
            continue
        _resolved_cli = cli
        return response
    if failures:
        raise RuntimeError(f'no usable OpenClaw CLI ({len(failures)} tried): {failures[0]}')
    raise RuntimeError(f'OpenClaw usage lookup exceeded its {FETCH_DEADLINE_SECONDS:g}s deadline')


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
    """Admit full exact windows; an incomplete read cannot erase saved data.

    Legacy metadata-less snapshots remain explicitly unverified. A partial
    first result may be retained for offline use, but only a complete read can
    replace it. No model maxima/unions or timestamps from failed reads are used.
    Background mode is request-driven and single-flight, not a scheduled job.
    """
    if background:
        return _background_load(days, start_date, end_date, cache_dir=cache_dir, fetch=fetch)
    fetch = fetch or fetch_usage
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


_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='dashboard-openclaw')
_flight_lock = threading.Lock()
_flights = {}
BACKGROUND_COLD_WAIT_SECONDS = 2
BACKGROUND_RESULT_TTL_SECONDS = 30
MAX_BACKGROUND_WINDOWS = 3


def _background_load(days, start_date, end_date, *, cache_dir=None, fetch=None):
    key = (str(db_file(cache_dir)), _window_key(days, start_date, end_date))
    record = read_snapshot(days, start_date, end_date, cache_dir=cache_dir)
    with _flight_lock:
        now = time.monotonic()
        for old_key, (old, timing) in list(_flights.items()):
            if old.done() and now - timing.get('finished_at', now) > BACKGROUND_RESULT_TTL_SECONDS:
                del _flights[old_key]
        entry = _flights.get(key)
        if entry and entry[0].done() and now - entry[1].get('finished_at', now) > BACKGROUND_RESULT_TTL_SECONDS:
            entry = None
        if not entry and sum(not f.done() for f, _ in _flights.values()) < MAX_BACKGROUND_WINDOWS:
            future = _pool.submit(load_with_cache, days, start_date, end_date,
                                  cache_dir=db_file(cache_dir), fetch=fetch or fetch_usage)
            timing = {}
            future.add_done_callback(lambda _future, clock=timing: clock.update(finished_at=time.monotonic()))
            entry = (future, timing)
            _flights[key] = entry
        future = entry[0] if entry else None
    if future is not None:
        try:
            # A saved window is available immediately; a truly cold request has
            # a small response budget while its one read continues in the pool.
            return future.result(timeout=0 if record else BACKGROUND_COLD_WAIT_SECONDS)
        except FutureTimeout:
            pass
    if record:
        return _saved_result(record, start_date, end_date, upstream_status='refreshing', refreshing=True)
    return {'status': 'refreshing', 'response': None, 'cached_at_utc': None,
            'gateway_cache_status': None, 'upstream_status': 'refreshing', 'error': None,
            'snapshot_written_at_utc': None, 'snapshot_complete': False,
            'source_at_utc': None, 'refreshing': True}


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
            'date': row['date'], 'input': None, 'output': None,
            'cache_read': None, 'cache_write': None, 'total': row['tokens'],
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
