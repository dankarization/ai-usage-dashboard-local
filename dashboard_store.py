"""Dashboard-owned SQLite database for aggregates the dashboard caches.

One file, created and owned by this dashboard: ``data/dashboard.sqlite``
(gitignored; parent directory ``0700``). It never reads or writes the OpenClaw
Gateway's own state database, Hermes ``state.db``, or the OmniRoute container's
``storage.sqlite``.

Rules that keep a cache from becoming a liability:

* the schema is additive and versioned in ``schema_meta``, so further tables can
  be added later without a breaking change;
* a database that is foreign, corrupt, or written by a newer schema is refused
  untouched: the caller degrades to ``unavailable`` instead of repairing or
  overwriting a file this dashboard does not own;
* a read never creates the file, so a dashboard that has never stored anything
  leaves no runtime state behind;
* each write is a single transaction, WAL-journaled with a busy timeout, so a
  second process or a concurrent page refresh cannot corrupt a stored window.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT / 'data' / 'dashboard.sqlite'
APP_ID = 'ai-usage-dashboard'
SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5000

_SCHEMA = (
    'CREATE TABLE IF NOT EXISTS schema_meta ('
    ' key TEXT PRIMARY KEY, value TEXT NOT NULL)',
    'CREATE TABLE IF NOT EXISTS openclaw_usage_snapshot ('
    ' window_key TEXT PRIMARY KEY, days INTEGER NOT NULL, start_date TEXT NOT NULL,'
    ' end_date TEXT NOT NULL, fetched_at_utc TEXT NOT NULL, payload TEXT NOT NULL)',
)


class StoreUnavailable(RuntimeError):
    """The dashboard database is foreign, corrupt, newer, or not writable."""


def db_path(path=None) -> Path:
    """The dashboard-owned database path; overridable for tests and proofs."""
    override = os.environ.get('AI_USAGE_DASHBOARD_DB')
    return Path(path or override or DEFAULT_DB_PATH)


def path_unusable(path: Path) -> bool:
    """A symlinked database or directory is refused rather than followed."""
    return path.is_symlink() or path.parent.is_symlink()


def window_key(days, start_date, end_date) -> str:
    """One key per exact period, so a window can never be confused with another."""
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValueError('Invalid usage window')
    for value in (start_date, end_date):
        if not isinstance(value, str):
            raise ValueError('Invalid usage window')
        datetime.strptime(value, '%Y-%m-%d')
    return f'days={days};start={start_date};end={end_date}'


def _classify(conn: sqlite3.Connection) -> str:
    """Whether the file is empty, ours, foreign, or from a newer dashboard."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if not tables:
        return 'empty'
    if 'schema_meta' not in tables:
        return 'foreign'
    marker = conn.execute("SELECT value FROM schema_meta WHERE key = 'app'").fetchone()
    if marker is None or marker[0] != APP_ID:
        return 'foreign'
    version = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    try:
        stored = int(version[0]) if version is not None else SCHEMA_VERSION
    except (TypeError, ValueError):
        return 'foreign'
    return 'ours' if stored <= SCHEMA_VERSION else 'newer'


def _create_schema(conn: sqlite3.Connection) -> None:
    """Idempotent setup: safe on a second start and on concurrent starts."""
    for statement in _SCHEMA:
        conn.execute(statement)
    conn.execute("INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('app', ?)",
                 (APP_ID,))
    conn.execute("INSERT OR IGNORE INTO schema_meta (key, value) VALUES ('created_at_utc', ?)",
                 (datetime.now(timezone.utc).isoformat(timespec='seconds'),))
    conn.execute(
        "INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(SCHEMA_VERSION),))


def _refuse(state: str) -> None:
    """Refuse a file this dashboard does not own, without changing it."""
    if state == 'foreign':
        raise StoreUnavailable('dashboard database is not owned by this dashboard')
    if state == 'newer':
        raise StoreUnavailable('dashboard database was written by a newer schema')


@contextmanager
def connection(path=None, *, create: bool):
    """Open the dashboard database, refusing a file that is not ours.

    Yields ``None`` when there is nothing to read yet (missing file, or an empty
    database with no write requested), so a read never creates runtime state.

    With ``create``, the caller's statements run inside one write transaction
    taken before the file is classified, so a second process either waits or
    sees a fully initialized database, never a half-created one.
    """
    target = db_path(path)
    if path_unusable(target):
        raise StoreUnavailable('dashboard database path is not usable')
    if create:
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    elif not target.exists():
        yield None
        return
    try:
        conn = sqlite3.connect(str(target), timeout=BUSY_TIMEOUT_MS / 1000)
    except sqlite3.Error as exc:
        raise StoreUnavailable(
            f'dashboard database unavailable ({type(exc).__name__})') from exc
    conn.isolation_level = None  # transactions are taken explicitly below
    try:
        conn.execute(f'PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}')
        try:
            state = _classify(conn)
        except sqlite3.Error as exc:
            # A file that is not a database at all is refused, not replaced.
            raise StoreUnavailable(
                f'dashboard database unreadable ({type(exc).__name__})') from exc
        _refuse(state)
        if state == 'empty' and not create:
            yield None
            return
        if state == 'empty':
            # The journal mode cannot change inside a transaction, so it is set
            # beforehand; it only applies to a database this dashboard creates.
            conn.execute('PRAGMA journal_mode = WAL')
        conn.execute('BEGIN IMMEDIATE')
        try:
            if create:
                state = _classify(conn)  # re-read under the writers' lock
                _refuse(state)
                if state == 'empty':
                    _create_schema(conn)
                    try:
                        os.chmod(target, 0o600)
                    except OSError:
                        pass
            yield conn
            conn.execute('COMMIT')
        except BaseException:
            try:
                conn.execute('ROLLBACK')
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.close()


def _prune(conn: sqlite3.Connection, keep: str, retention_seconds: float,
           window_limit: int) -> None:
    """Bound stored history: expired rows go, then everything past the newest set."""
    if retention_seconds:
        cutoff = datetime.fromtimestamp(
            datetime.now(timezone.utc).timestamp() - retention_seconds,
            timezone.utc).isoformat(timespec='seconds')
        conn.execute('DELETE FROM openclaw_usage_snapshot'
                     ' WHERE window_key != ? AND fetched_at_utc < ?', (keep, cutoff))
    conn.execute(
        'DELETE FROM openclaw_usage_snapshot WHERE window_key NOT IN ('
        ' SELECT window_key FROM openclaw_usage_snapshot'
        ' ORDER BY fetched_at_utc DESC, rowid DESC LIMIT ?)', (window_limit,))


def store_snapshot(path, key, days, start_date, end_date, fetched_at_utc, payload,
                   *, retention_seconds: float, window_limit: int,
                   replace: bool = True) -> None:
    """Save one window in a single transaction, then bound the table."""
    verb = 'INSERT INTO' if replace else 'INSERT OR IGNORE INTO'
    statement = (
        f'{verb} openclaw_usage_snapshot'
        ' (window_key, days, start_date, end_date, fetched_at_utc, payload)'
        ' VALUES (?, ?, ?, ?, ?, ?)' + (
            ' ON CONFLICT(window_key) DO UPDATE SET days = excluded.days,'
            ' start_date = excluded.start_date, end_date = excluded.end_date,'
            ' fetched_at_utc = excluded.fetched_at_utc, payload = excluded.payload'
            if replace else ''))
    with connection(path, create=True) as conn:
        conn.execute(statement, (key, days, start_date, end_date, fetched_at_utc,
                                 payload))
        _prune(conn, key, retention_seconds, window_limit)


def read_snapshot(path, key):
    """The saved row for one exact window key, or ``None``."""
    with connection(path, create=False) as conn:
        if conn is None:
            return None
        row = conn.execute(
            'SELECT days, start_date, end_date, fetched_at_utc, payload'
            ' FROM openclaw_usage_snapshot WHERE window_key = ?', (key,)).fetchone()
    if row is None:
        return None
    return {'days': row[0], 'start_date': row[1], 'end_date': row[2],
            'fetched_at_utc': row[3], 'payload': row[4]}
