"""Tests for the self-contained dashboard page.

Two layers:

* Static invariants: the page must not pull external assets or use unsafe DOM
  APIs, and it must keep the endpoints the service actually serves.
* Behavioural contract: the real dashboard script is executed against a small
  DOM shim (``tests/dashboard_dom_harness.js``) so quota semantics are asserted
  on rendered output rather than on source substrings.
"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

import local_display_service
from dashboard_page import DASHBOARD_HTML

HARNESS = Path(__file__).resolve().parent / 'dashboard_dom_harness.js'


def _extract_script(html: str) -> str:
    match = re.search(r'<script>(.*?)</script>', html, re.S)
    assert match, 'dashboard HTML must contain an inline script'
    return match.group(1)


def test_page_has_no_external_assets():
    for needle in ('<script src=', '<link ', 'innerHTML', 'document.write', 'insertAdjacentHTML', 'cdn.'):
        assert needle not in DASHBOARD_HTML, f'dashboard must not use {needle!r}'
    # The only absolute URL may be the SVG namespace, which is not a network fetch.
    urls = {u for u in re.findall(r'https?://[^\s"\')]+', DASHBOARD_HTML)}
    assert urls <= {'http://www.w3.org/2000/svg'}, f'unexpected external URL(s): {sorted(urls)}'


def test_page_exposes_responsive_and_accessibility_hooks():
    assert 'viewport' in DASHBOARD_HTML
    assert '@media (max-width:640px)' in DASHBOARD_HTML
    assert 'prefers-reduced-motion' in DASHBOARD_HTML
    # Quota bars are real progressbars with an accessible name and value.
    assert "setAttribute('role', 'progressbar')" in DASHBOARD_HTML
    assert 'aria-valuenow' in DASHBOARD_HTML
    assert 'aria-label' in DASHBOARD_HTML


def test_page_calls_the_live_endpoints():
    for endpoint in ('/api/v1/quotas', '/api/v1/model-breakdown?days=',
                     '/api/v1/display/update'):
        assert endpoint in DASHBOARD_HTML, f'dashboard must call {endpoint}'


def test_section_notes_do_not_repeat_period_selection():
    assert 'id="history-note"' not in DASHBOARD_HTML
    for note_id in ('overview-note', 'models-note'):
        note = re.search(rf'<p class="note" id="{note_id}">(.*?)</p>', DASHBOARD_HTML)
        assert note is not None
        assert not re.search(r'\bselected\s+(?:7|30)d\b', note.group(1), re.I)
    assert 'OpenClaw non-NordRouter + direct NordRouter daily tokens' not in DASHBOARD_HTML


def test_page_orders_quotas_codex_then_grok_then_rest():
    # The requested hierarchy is overall statistics, then Codex accounts, then
    # Grok, then NordRouter. The page markup must place the sections in order.
    order = [DASHBOARD_HTML.index(marker) for marker in
             ('id="hero"', 'id="codex"', 'id="quotas"', 'id="h-nord"', 'id="history"')]
    assert order == sorted(order), 'sections must be stats, Codex, Grok, NordRouter, history'
    assert "var QUOTA_ORDER = ['codex', 'grok']" in DASHBOARD_HTML


def test_page_marks_brand_and_colors_by_remaining_capacity():
    # Inline text brand marks keep the page asset-free.
    assert 'function brandMark(' in DASHBOARD_HTML
    assert "codex: 'OI'" in DASHBOARD_HTML
    assert "grok: 'xAI'" in DASHBOARD_HTML
    assert 'brandtile' in DASHBOARD_HTML
    # Colour thresholds follow how close the window is to exhaustion.
    assert "used >= 80 ? 'danger' : (used >= 50 ? 'warn' : 'ok')" in DASHBOARD_HTML


def test_page_labels_list_price_estimate_and_never_fakes_it():
    assert 'OpenClaw estimated cost' in DASHBOARD_HTML
    assert 'NordRouter billed cost' in DASHBOARD_HTML
    assert 'excludes NordRouter route' in DASHBOARD_HTML
    assert 'id="period-30"' in DASHBOARD_HTML
    assert 'id="period-7"' in DASHBOARD_HTML
    assert 'id="h-models">Model Usage' in DASHBOARD_HTML
    assert 'id="h-costs"' not in DASHBOARD_HTML
    assert 'id="cost-models"' not in DASHBOARD_HTML
    assert 'NordRouter model costs' not in DASHBOARD_HTML


def test_history_chart_is_tall_on_desktop_and_mobile():
    assert 'svg.chart{display:block;width:100%;height:300px}' in DASHBOARD_HTML
    assert 'svg.chart{height:260px}' in DASHBOARD_HTML


def test_page_shows_configured_and_unconfigured_account_states():
    assert 'Not configured' in DASHBOARD_HTML
    assert 'No percentage reported for this window.' in DASHBOARD_HTML


def test_page_renders_snapshot_time_from_offset_aware_field():
    """The header must not present a naive server timestamp as local time.

    generated_at is Pacific wall clock with no offset, so a browser would read
    it as its own local time and show the wrong hour. The page must prefer the
    offset-aware generated_at_utc and label a legacy value as server time.
    """
    assert 'function snapshotLabel(' in DASHBOARD_HTML
    assert 'generated_at_utc' in DASHBOARD_HTML
    assert 'server time' in DASHBOARD_HTML
    assert 'function relativeAge(' in DASHBOARD_HTML
    # A stale snapshot must be marked rather than presented as current.
    assert 'STALE_AFTER_MS' in DASHBOARD_HTML
    assert 'stale' in DASHBOARD_HTML


def test_page_distinguishes_unavailable_from_zero():
    # An unavailable window must render an explicit explanation, never a 0% bar.
    assert "'Used: —'" in DASHBOARD_HTML
    assert 'No percentage reported for this window.' in DASHBOARD_HTML
    assert "'—'" in DASHBOARD_HTML
    # NordRouter is excluded from the quota grid and rendered in its own section.
    assert "row.provider !== 'nordrouter' && row.provider !== 'codex'" in DASHBOARD_HTML


def test_page_draws_history_only_from_real_buckets():
    assert 'No historical usage data available.' in DASHBOARD_HTML
    assert 'payload.source_daily' in DASHBOARD_HTML
    # No fabricated series, token totals, or placeholder chart data.
    assert 'Math.random' not in DASHBOARD_HTML
    assert 'lorem' not in DASHBOARD_HTML.lower()


def test_dashboard_routes_serve_the_page():
    from fastapi.testclient import TestClient

    client = TestClient(local_display_service.app)
    for route in ('/', '/dashboard'):
        response = client.get(route)
        assert response.status_code == 200
        assert response.headers['content-type'].startswith('text/html')
        assert 'AI Usage Dashboard' in response.text


@pytest.mark.skipif(shutil.which('node') is None, reason='node is required for the DOM harness')
def test_dashboard_script_satisfies_rendered_contract(tmp_path):
    script_path = tmp_path / 'dashboard.js'
    script_path.write_text(_extract_script(DASHBOARD_HTML))
    result = subprocess.run(
        ['node', str(HARNESS), str(script_path)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f'DOM harness failed:\n{result.stdout}\n{result.stderr}'
    assert 'OK: dashboard DOM contract holds' in result.stdout
