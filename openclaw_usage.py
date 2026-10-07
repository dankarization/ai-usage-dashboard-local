"""Read Gateway usage aggregates without reading private transcript content.

The Gateway is the authority for OpenClaw session usage. Its modelDaily rows
contain total tokens/cost only; byModel supplies token-type window totals.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


def _cli() -> str:
    override = os.environ.get('OPENCLAW_USAGE_CLI')
    if override:
        return override
    installations = Path.home() / '.local/share/openclaw-installations'
    candidates = sorted(installations.glob('*/bin/openclaw'), reverse=True)
    if not candidates:
        raise RuntimeError('OpenClaw CLI installation unavailable')
    return str(candidates[0])


def fetch_usage(start_date: str, end_date: str) -> dict:
    """Fetch aggregate-only all-agent usage; no session labels or text retained."""
    params = {'agentScope': 'all', 'limit': 1,
              'startDate': start_date, 'endDate': end_date}
    result = subprocess.run(
        [_cli(), 'gateway', 'call', 'sessions.usage', '--json',
         '--params', json.dumps(params), '--timeout', '60000'],
        capture_output=True, text=True, timeout=75, check=True,
    )
    response = json.loads(result.stdout)
    if not isinstance(response, dict) or not isinstance(response.get('aggregates'), dict):
        raise RuntimeError('OpenClaw usage aggregate unavailable')
    return response


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
        if raw_totals.get('missingCostEntries') or not isinstance(modeled_cost, (int, float)):
            modeled_cost = None
        entries.append({
            'source': 'openclaw', 'provider': provider, 'model': model,
            'totals': totals,
            'daily': daily if include_daily else [],
            # Gateway cost is a model-price estimate, not a provider invoice.
            'cost_usd': modeled_cost,
        })
    return entries
