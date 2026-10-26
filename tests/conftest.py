"""Test isolation for the dashboard suite.

Two properties the suite needs and did not have:

* The dashboard reads a private ``.env`` at runtime, and several suites drive
  service code paths that call ``load_env()``. Those values must not survive
  into unrelated tests, otherwise results depend on file execution order.
* No test may write the live OpenClaw saved-window database under
  ``data/dashboard.sqlite``: a test fixture must never become data the running
  service later serves as a saved usage window.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path):
    """Restore the process environment and redirect the saved-window database."""
    before = dict(os.environ)
    os.environ['OPENCLAW_USAGE_CACHE_DIR'] = str(tmp_path / 'openclaw-cache')
    os.environ['AI_USAGE_DASHBOARD_DB'] = str(tmp_path / 'dashboard.sqlite')
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(before)
