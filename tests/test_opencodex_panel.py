import json
from pathlib import Path
import shutil
import subprocess

from fastapi.testclient import TestClient
import pytest

import opencodex_collect
import opencodex_panel
import opencodex_usage as usage
from dashboard_page import DASHBOARD_HTML
import local_display_service as service


def test_disabled_leaves_existing_page_identical_and_has_no_writes(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENCODEX_USAGE_ENABLED', raising=False)
    monkeypatch.setenv('OPENCODEX_USAGE_DB', str(tmp_path / 'not-created.sqlite'))
    assert opencodex_panel.extend(DASHBOARD_HTML) == DASHBOARD_HTML
    client = TestClient(service.app)
    assert client.get('/api/v1/opencodex').json()['status'] == 'disabled'
    assert not (tmp_path / 'not-created.sqlite').exists()
    assert 'id="h-opencodex"' not in client.get('/').text


def test_cache_only_endpoint_and_opt_in_panel_do_not_collect_or_join_totals(tmp_path, monkeypatch):
    path = tmp_path / 'owned.sqlite'
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    monkeypatch.setenv('OPENCODEX_USAGE_SOURCE_ID', 'fixture-mac')
    monkeypatch.setenv('OPENCODEX_USAGE_DB', str(path))
    export = json.loads((Path(__file__).parent / 'fixtures/opencodex-usage-v1.json').read_text())
    usage.import_snapshot(export)
    def forbidden(*args, **kwargs):
        raise AssertionError('A page/API read must not trigger collection')
    monkeypatch.setattr(opencodex_collect, 'collect', forbidden)
    monkeypatch.setattr(opencodex_collect, '_fetch', forbidden)
    before = path.read_bytes()
    client = TestClient(service.app)
    response = client.get('/api/v1/opencodex?days=1')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['additive'] is False
    assert response.json()['daily'][0]['total_tokens'] == 300
    html = client.get('/').text
    assert 'id="h-opencodex"' in html
    assert 'not added to overview' in html
    assert 'mergeModels(' not in opencodex_panel.SCRIPT
    assert 'historyRows(' not in opencodex_panel.SCRIPT
    assert client.get('/api/v1/opencodex?days=0').status_code == 200
    assert client.get('/api/v1/opencodex?days=2').status_code == 422
    assert client.post('/api/v1/opencodex').status_code == 405
    assert path.read_bytes() == before


def test_enabled_extension_safe_and_existing_rendering_unchanged(monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED', '1')
    page = opencodex_panel.extend(DASHBOARD_HTML)
    for needle in ('innerHTML', 'insertAdjacentHTML', 'document.write', '<script src='):
        assert needle not in page
    assert page.count('id="h-opencodex"') == 1
    assert page.count('<script>') == page.count('</script>') == 1
    assert page.count('id="history-source"') == 1
    assert '<option value="opencodex">OpenCodex на Macbook</option>' in page
    assert '<option value="hermes">Hermes</option>' in page
    assert '<option value="openclaw">OpenClaw</option>' in page
    assert 'opencodex-panel' not in DASHBOARD_HTML


def test_real_panel_script_graph_and_offline_rendering(tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is needed for the existing offline DOM-shim approach')
    script = tmp_path / 'opencodex.js'
    script.write_text(opencodex_panel.SCRIPT)
    result = subprocess.run([node, str(Path(__file__).parent / 'opencodex_dom_harness.js'), str(script)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'assertions passed' in result.stdout
