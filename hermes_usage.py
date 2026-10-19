"""Read Hermes native usage aggregates without reading transcript content.

Hermes records per-session model accounting in ``session_model_usage`` inside
each profile's ``state.db``. A row carries the model, its billing route, token
type counts and the cost columns Hermes itself recorded; message text and
transcripts are never read here.

Rows are session/model/route aggregates, not per-day buckets. A row whose
activity spans midnight is attributed to the day of its last activity, which
keeps the daily series honest about where the data comes from instead of
inventing a split.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

from pricing_config import calc_cost, get_pricing

# Routes that bill through an aggregator the dashboard already reads directly.
# Their tokens are counted once by that account, so rows here stay comparison-only.
NORDROUTER_HOST_SUFFIX = 'nordrouter.com'
OMNIROUTE_PORTS = frozenset({20128})
# Hermes reports the provider family; keep the dashboard's shorter route names.
PROVIDER_ALIASES = {'openai-codex': 'openai'}

_COLUMNS = (
    'model', 'billing_provider', 'billing_base_url',
    'input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_write_tokens',
    'first_seen', 'last_seen',
)


def hermes_root() -> Path:
    """Locate the Hermes root that owns ``state.db`` and ``profiles/``.

    A profile-scoped process sets ``HERMES_HOME`` to ``<root>/profiles/<name>``;
    Hermes anchors the profile store to the default root, so walk one level up
    when the override names a profile.
    """
    override = os.environ.get('HERMES_HOME', '').strip()
    if override:
        base = Path(override).expanduser()
        if base.parent.name == 'profiles':
            return base.parent.parent
        return base
    for candidate in (Path.home() / '.hermes-native', Path.home() / '.hermes'):
        if (candidate / 'state.db').is_file() or (candidate / 'profiles').is_dir():
            return candidate
    return Path.home() / '.hermes'


def state_dbs(root: Path | None = None) -> list[Path]:
    """Every profile database, default profile first, then named profiles."""
    base = Path(root) if root is not None else hermes_root()
    found: list[Path] = []
    if (base / 'state.db').is_file():
        found.append(base / 'state.db')
    profiles = base / 'profiles'
    if profiles.is_dir():
        try:
            entries = sorted(profiles.iterdir())
        except OSError:
            entries = []
        found.extend(entry / 'state.db' for entry in entries
                     if (entry / 'state.db').is_file())
    return found


def _connect(path: Path) -> sqlite3.Connection:
    """Open read-only; fall back to a read-write handle only to read WAL data.

    A read-only handle cannot create the ``-shm`` sidecar when it is missing, so
    a live WAL database occasionally rejects it. The fallback still issues
    SELECT statements only.
    """
    try:
        return sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5)
    except sqlite3.Error:
        return sqlite3.connect(str(path), timeout=5)


def _route(provider: str, base_url: str) -> str:
    """Collapse Hermes' billing identity into the dashboard's route vocabulary."""
    provider = (provider or '').strip().lower()
    host, port = '', None
    try:
        parsed = urlparse(base_url or '')
        host, port = (parsed.hostname or '').lower(), parsed.port
    except ValueError:
        pass
    if provider == 'nordrouter' or host.endswith(NORDROUTER_HOST_SUFFIX):
        return 'nordrouter'
    if port in OMNIROUTE_PORTS:
        return 'omniroute'
    return PROVIDER_ALIASES.get(provider, provider) or 'hermes'


def _row_date(first_seen: float | None, last_seen: float | None) -> date | None:
    """Local day of the row's last activity; the dashboard windows are local."""
    stamp = last_seen if last_seen is not None else first_seen
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(float(stamp)).date()
    except (OverflowError, OSError, ValueError):
        return None


def _estimate_cost(model: str, tokens: dict[str, int]) -> float | None:
    """Price tokens from the shared table, which also prices OpenClaw rows.

    Hermes records no cost of its own. Rather than invent a rate, reuse the same
    audited table the other agent rollups are priced with, so an overlapping
    model carries one consistent estimate across both agents. An unpriced model
    stays unpriced.
    """
    pricing = get_pricing(model)
    if not pricing and '/' in model:
        pricing = get_pricing(model.split('/', 1)[1])
    if not pricing:
        return None
    return calc_cost(
        pricing,
        input_tokens=tokens['input'] + tokens['cache_read'],
        output_tokens=tokens['output'],
        cached_tokens=tokens['cache_read'],
        cache_write_tokens=tokens['cache_write'],
    )


def model_entries(start_date: str, end_date: str, *, include_daily: bool = True,
                  root: Path | None = None) -> list[dict]:
    """Per-model Hermes usage for the inclusive window, one entry per route."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    totals: dict[tuple[str, str], dict] = {}
    daily: dict[tuple[str, str], dict[str, dict[str, int]]] = {}

    for path in state_dbs(root):
        try:
            connection = _connect(path)
        except sqlite3.Error:
            continue
        try:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM session_model_usage").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            connection.close()
        for row in rows:
            model = (row['model'] or '').strip()
            if not model:
                continue
            tokens = {
                'input': int(row['input_tokens'] or 0), 'output': int(row['output_tokens'] or 0),
                'cache_read': int(row['cache_read_tokens'] or 0),
                'cache_write': int(row['cache_write_tokens'] or 0),
            }
            tokens['total'] = sum(tokens.values())
            if tokens['total'] <= 0:
                continue
            day = _row_date(row['first_seen'], row['last_seen'])
            if day is not None and not (start <= day <= end):
                continue
            route = _route(row['billing_provider'], row['billing_base_url'])
            key = (model, route)
            bucket = totals.setdefault(key, {
                'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 0,
            })
            for field in ('input', 'output', 'cache_read', 'cache_write', 'total'):
                bucket[field] += tokens[field]
            if include_daily and day is not None:
                days = daily.setdefault(key, {})
                entry = days.setdefault(day.isoformat(),
                                        {'input': 0, 'output': 0, 'cache_read': 0,
                                         'cache_write': 0, 'total': 0})
                for field in ('input', 'output', 'cache_read', 'cache_write', 'total'):
                    entry[field] += tokens[field]

    entries = []
    for (model, route), bucket in totals.items():
        # NordRouter is billed by its own account API, so a routed row here is a
        # comparison figure and must not claim a second price. Every other model
        # is priced from the shared table, the same source OpenClaw rows use.
        cost_usd, cost_kind = None, None
        if route != 'nordrouter':
            cost_usd = _estimate_cost(model, bucket)
            cost_kind = 'estimated' if cost_usd is not None else None
        days = daily.get((model, route), {})
        entries.append({
            'source': 'hermes', 'provider': route, 'model': model,
            'totals': {field: bucket[field] for field in
                       ('input', 'output', 'cache_read', 'cache_write', 'total')},
            'daily': [dict(date=day, **days[day]) for day in sorted(days)],
            'cost_usd': cost_usd, 'cost_kind': cost_kind,
        })
    entries.sort(key=lambda entry: entry['totals']['total'], reverse=True)
    return entries
