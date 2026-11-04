"""The removed runtime transport cannot be revived by configuration."""
import pytest
import openclaw_usage


def test_live_transport_removed(monkeypatch):
    monkeypatch.setenv('OPENCLAW_USAGE_CLI', '/must-not-execute')
    with pytest.raises(RuntimeError, match='transport removed'):
        openclaw_usage.fetch_usage('2026-10-01','2026-10-07')
