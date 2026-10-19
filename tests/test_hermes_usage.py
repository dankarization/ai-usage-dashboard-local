"""Hermes native usage reader.

The reader must stay honest about what Hermes records: token counts per
session/model/route, a single activity day per aggregate row, and no price of
its own.
"""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

import hermes_usage


def make_db(path, rows):
    """Create a minimal state.db with the columns the reader selects."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("""
        CREATE TABLE session_model_usage (
            session_id TEXT NOT NULL,
            model TEXT NOT NULL,
            billing_provider TEXT NOT NULL DEFAULT '',
            billing_base_url TEXT NOT NULL DEFAULT '',
            billing_mode TEXT NOT NULL DEFAULT '',
            task TEXT NOT NULL DEFAULT '',
            api_call_count INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_read_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_usd REAL NOT NULL DEFAULT 0,
            actual_cost_usd REAL NOT NULL DEFAULT 0,
            cost_status TEXT, cost_source TEXT,
            first_seen REAL, last_seen REAL
        )""")
    for row in rows:
        connection.execute(
            "INSERT INTO session_model_usage (session_id, model, billing_provider,"
            " billing_base_url, input_tokens, output_tokens, cache_read_tokens,"
            " cache_write_tokens, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row.get('session', 's1'), row['model'], row.get('provider', ''),
             row.get('base_url', ''), row.get('input', 0), row.get('output', 0),
             row.get('cache_read', 0), row.get('cache_write', 0),
             row.get('first_seen'), row.get('last_seen')))
    connection.commit()
    connection.close()


def stamp(day):
    """Noon local time on the given date, as epoch seconds."""
    return datetime.combine(day, datetime.min.time()).replace(hour=12).timestamp()


def test_state_dbs_covers_default_and_named_profiles(tmp_path):
    make_db(tmp_path / 'state.db', [])
    make_db(tmp_path / 'profiles' / 'coder' / 'state.db', [])
    make_db(tmp_path / 'profiles' / 'wiki' / 'state.db', [])
    assert hermes_usage.state_dbs(tmp_path) == [
        tmp_path / 'state.db',
        tmp_path / 'profiles' / 'coder' / 'state.db',
        tmp_path / 'profiles' / 'wiki' / 'state.db',
    ]


def test_hermes_root_walks_up_from_a_profile_scoped_home(monkeypatch, tmp_path):
    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'profiles' / 'coder'))
    assert hermes_usage.hermes_root() == tmp_path


def test_rows_are_grouped_per_model_and_route(tmp_path):
    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'z-ai/glm-5.3', 'provider': 'nordrouter',
         'base_url': 'https://nordrouter.com/v1', 'input': 100, 'output': 20,
         'cache_read': 300, 'last_seen': stamp(today)},
        # Same model billed through a custom provider that still points at NordRouter.
        {'model': 'z-ai/glm-5.3', 'provider': 'custom',
         'base_url': 'https://nordrouter.com/v1/', 'input': 10, 'output': 5,
         'last_seen': stamp(today)},
        {'model': 'gpt-5.6-sol', 'provider': 'openai-codex',
         'base_url': 'https://chatgpt.com/backend-api/codex', 'input': 7,
         'output': 3, 'last_seen': stamp(today)},
    ])
    entries = hermes_usage.model_entries(today.isoformat(), today.isoformat(), root=tmp_path)
    by_key = {(entry['model'], entry['provider']): entry for entry in entries}
    assert set(by_key) == {('z-ai/glm-5.3', 'nordrouter'), ('gpt-5.6-sol', 'openai')}
    assert by_key[('z-ai/glm-5.3', 'nordrouter')]['totals']['total'] == 435
    assert by_key[('gpt-5.6-sol', 'openai')]['totals']['total'] == 10
    assert all(entry['source'] == 'hermes' for entry in entries)


def test_omniroute_port_is_its_own_route(tmp_path):
    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'free-final-fallback', 'provider': 'custom',
         'base_url': 'http://omniroute.internal:20128/v1/', 'input': 5, 'output': 5,
         'last_seen': stamp(today)},
    ])
    entries = hermes_usage.model_entries(today.isoformat(), today.isoformat(), root=tmp_path)
    assert [entry['provider'] for entry in entries] == ['omniroute']


def test_window_excludes_rows_outside_the_requested_days(tmp_path):
    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'inside', 'provider': 'openai', 'input': 1, 'last_seen': stamp(today)},
        {'model': 'outside', 'provider': 'openai', 'input': 1,
         'last_seen': stamp(today - timedelta(days=40))},
    ])
    entries = hermes_usage.model_entries((today - timedelta(days=6)).isoformat(),
                                         today.isoformat(), root=tmp_path)
    assert [entry['model'] for entry in entries] == ['inside']


def test_multi_day_row_is_attributed_to_its_last_activity_day(tmp_path):
    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'spans', 'provider': 'openai', 'input': 10,
         'first_seen': stamp(today - timedelta(days=1)), 'last_seen': stamp(today)},
    ])
    entries = hermes_usage.model_entries((today - timedelta(days=6)).isoformat(),
                                         today.isoformat(), root=tmp_path)
    assert [day['date'] for day in entries[0]['daily']] == [today.isoformat()]


def test_named_profiles_are_summed_into_one_entry(tmp_path):
    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'shared', 'provider': 'openai', 'input': 10, 'last_seen': stamp(today)}])
    make_db(tmp_path / 'profiles' / 'coder' / 'state.db', [
        {'model': 'shared', 'provider': 'openai', 'input': 15, 'last_seen': stamp(today)}])
    entries = hermes_usage.model_entries(today.isoformat(), today.isoformat(), root=tmp_path)
    assert len(entries) == 1
    assert entries[0]['totals']['total'] == 25
    assert entries[0]['daily'] == [
        {'date': today.isoformat(), 'input': 25, 'output': 0, 'cache_read': 0,
         'cache_write': 0, 'total': 25}]


def test_nordrouter_rows_never_claim_a_second_price(tmp_path):
    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'z-ai/glm-5.3', 'provider': 'nordrouter', 'input': 1_000_000,
         'last_seen': stamp(today)},
    ])
    entries = hermes_usage.model_entries(today.isoformat(), today.isoformat(), root=tmp_path)
    assert entries[0]['cost_usd'] is None
    assert entries[0]['cost_kind'] is None


def test_priced_models_use_the_shared_table_and_unpriced_stay_unpriced(tmp_path):
    """Hermes records no cost; the shared table is the single price source."""
    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'z-ai/glm-5.3', 'provider': 'custom',
         'base_url': 'https://nordrouter.com.example/v1', 'input': 1_000_000,
         'last_seen': stamp(today)},
        {'model': 'gpt-6-sol-900k', 'provider': 'openai', 'input': 1_000_000,
         'last_seen': stamp(today)},
    ])
    entries = hermes_usage.model_entries(today.isoformat(), today.isoformat(), root=tmp_path)
    priced = {entry['model']: entry for entry in entries}
    assert priced['z-ai/glm-5.3']['cost_usd'] == pytest.approx(1.4)
    assert priced['z-ai/glm-5.3']['cost_kind'] == 'estimated'
    # No audited rate for this model id: report unknown, never a zero price.
    assert priced['gpt-6-sol-900k']['cost_usd'] is None
    assert priced['gpt-6-sol-900k']['cost_kind'] is None


def test_empty_databases_yield_no_entries(tmp_path):
    make_db(tmp_path / 'state.db', [])
    assert hermes_usage.model_entries(date.today().isoformat(), date.today().isoformat(),
                                      root=tmp_path) == []


def test_missing_root_is_not_an_error(tmp_path):
    assert hermes_usage.state_dbs(tmp_path / 'absent') == []
    assert hermes_usage.model_entries(date.today().isoformat(), date.today().isoformat(),
                                      root=tmp_path / 'absent') == []


def test_breakdown_adds_hermes_and_keeps_nordrouter_comparison_only(monkeypatch, tmp_path):
    """Hermes rows join the canonical total; its NordRouter route stays a comparison."""
    import auto_usage
    import openclaw_usage

    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'z-ai/glm-5.3', 'provider': 'nordrouter', 'input': 100,
         'last_seen': stamp(today)},
        {'model': 'gpt-5.6-sol', 'provider': 'openai-codex',
         'base_url': 'https://chatgpt.com/backend-api/codex', 'input': 40, 'output': 10,
         'last_seen': stamp(today)},
    ])
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('NORDROUTER_API_KEY', raising=False)
    monkeypatch.setattr(openclaw_usage, 'fetch_usage',
                        lambda *a: (_ for _ in ()).throw(RuntimeError('gateway down')))
    monkeypatch.setattr(auto_usage, 'get_date_range',
                        lambda days: (today.isoformat(), today.isoformat(), None, None))

    result = auto_usage.build_model_breakdown(days=1, source='hermes')

    assert result['meta']['hermes_status'] == 'ok'
    assert result['meta']['openclaw_status'] == 'pending'
    assert {row['model'] for row in result['models']} == {'z-ai/glm-5.3', 'gpt-5.6-sol'}
    # The NordRouter route is counted once by the account API, never twice here.
    assert result['sources']['hermes']['tokens'] == 50
    assert result['sources']['hermes_nordrouter_comparison_tokens'] == 100
    assert result['sources']['openclaw_nordrouter_comparison_tokens'] == 0


def test_hermes_rows_are_priced_at_the_rate_another_source_reports(monkeypatch, tmp_path):
    """Hermes reports no cost, so its rows borrow the per-token rate of the same model.

    OpenClaw bills gpt-6-sol at a known amount for a known token count; Hermes
    usage of the same model is priced at that ratio instead of staying blank.
    """
    import auto_usage
    import openclaw_usage

    today = date.today()
    make_db(tmp_path / 'state.db', [
        {'model': 'gpt-6-sol', 'provider': 'openai', 'input': 1_000, 'last_seen': stamp(today)},
        {'model': 'unknown-model', 'provider': 'openai', 'input': 500, 'last_seen': stamp(today)},
    ])
    monkeypatch.setenv('HERMES_HOME', str(tmp_path))
    monkeypatch.delenv('NORDROUTER_API_KEY', raising=False)
    monkeypatch.setattr(openclaw_usage, 'fetch_usage', lambda *a: {})
    monkeypatch.setattr(openclaw_usage, 'model_entries', lambda *a, **k: [
        {'source': 'openclaw', 'provider': 'openai', 'model': 'gpt-6-sol',
         'totals': {'input': 9_000, 'output': 0, 'cache_read': 0, 'cache_write': 0, 'total': 9_000},
         'daily': [], 'cost_usd': 9.0, 'cost_kind': 'estimated'},
    ])
    monkeypatch.setattr(auto_usage, 'get_date_range',
                        lambda days: (today.isoformat(), today.isoformat(), None, None))

    result = auto_usage.build_model_breakdown(days=1, source='all')

    rows = {row['model']: row for row in result['models'] if row['source'] == 'hermes'}
    # 1_000 tokens at the 9.0 / 9_000 rate OpenClaw reports for the same model.
    assert rows['gpt-6-sol']['cost_usd'] == pytest.approx(1.0)
    assert rows['gpt-6-sol']['cost_kind'] == 'estimated'
    # A model nobody priced stays unknown rather than guessed.
    assert rows['unknown-model']['cost_usd'] is None


def test_breakdown_reports_hermes_as_not_configured_without_state_db(monkeypatch, tmp_path):
    import auto_usage

    monkeypatch.setenv('HERMES_HOME', str(tmp_path / 'absent'))
    monkeypatch.delenv('NORDROUTER_API_KEY', raising=False)
    result = auto_usage.build_model_breakdown(days=1, source='hermes')
    assert result['meta']['hermes_status'] == 'not_configured'
    assert result['models'] == []
    assert result['sources']['hermes']['tokens'] == 0
