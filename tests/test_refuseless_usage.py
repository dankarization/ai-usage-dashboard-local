"""Provider identity, unknown metrics and non-additive Refuseless view."""
import json
from pathlib import Path
import subprocess

import pytest
import refuseless_usage as ref


def row(**kw):
    return dict(date='2026-11-23', bucket='refuseless', provider='custom-chat',
                model='vendor/model-a', usage_provenance='reported', status=200,
                requests=1, prompt=100, output=20, cache_read=0, cache_write=0,
                invalid=0, **kw)


def test_summary_reported_tokens_missing_tariff_and_no_billing():
    body = {'identity': 'connection_endpoint', 'providers': {'custom-chat': False},
            'rows': [row()]}
    result = ref.summarize(body, 'ok', '2026-11-23')
    assert result['today']['tokens'] == 120
    assert result['period']['models'][0]['id'] == 'model-a'
    assert result['today']['amount_usd'] is None
    assert result['balance_usd'] is result['billed_cost_usd'] is None
    assert result['additive'] is False


def test_estimated_cost_today_and_retained_period(monkeypatch):
    import pricing_config
    monkeypatch.setattr(pricing_config, 'api_pricing', lambda *_: ('model-a', {'input': 2, 'output': 10}))
    old = row(); old['date'] = '2026-08-01'
    result = ref.summarize({'identity': 'connection_endpoint', 'providers': {},
                            'rows': [old, row()]}, 'stale', '2026-11-23')
    assert result['today']['amount_usd'] == pytest.approx(.0004)
    assert result['period']['amount_usd'] == pytest.approx(.0008)
    assert result['period']['tokens'] == 240
    assert result['start_date'] == '2026-08-01'
    assert result['status'] == 'stale'
    assert len(result['daily']) == 2


@pytest.mark.parametrize('status', ['unavailable', 'not_configured', 'stale'])
def test_unavailable_not_zero(status):
    result = ref.summarize(None, status, '2026-11-23')
    assert result['today'] is result['period'] is None
    assert result['daily'] == []


def test_missing_reported_usage_stays_unknown():
    missing = row(); missing['usage_provenance'] = 'estimated'
    result = ref.summarize({'identity': 'connection_endpoint', 'providers': {},
                            'rows': [missing]}, 'ok', '2026-11-23')
    assert result['period']['tokens'] is None
    assert result['period']['amount_usd'] is None


def test_connection_identity_excludes_nordrouter_and_other_omniroute(tmp_path):
    script = Path('scripts/refuseless-aggregate.cjs').read_text()
    # Execute the actual connection classification and binding code with a tiny
    # aggregate DB fixture; even identical model/provider IDs cannot admit others.
    runner = r'''
const vm = require('vm');
const connections = [
 {id:'ref',url:'https://api.refuseless.com/v1'},
 {id:'nord',url:'https://nordrouter.com/v1'},
 {id:'other',url:'https://api.openai.com/v1'},
 {id:'spoof',url:'https://api.refuseless.com.attacker.test/v1'},
 {id:'http',url:'http://api.refuseless.com/v1'}];
const rows = connections.map(c => ({connection_id:c.id,provider:'same-alias',model:'same-model'}));
const db = {pragma(){},close(){},transaction(fn){return fn;},prepare(sql){return {all(...args){
 if (sql.includes('FROM provider_connections')) return connections;
 if (!sql.includes('connection_id IN') || sql.includes('combo_name IS NOT NULL')) throw Error('unscoped SQL');
 return rows.filter(r => args.slice(0,-1).includes(r.connection_id));
}};}};
const fakeRequire = name => name === 'fs' ? {readFileSync:()=>JSON.stringify({end:'2026-11-24T00:00:00+00:00'})} : function(){return db;};
vm.runInNewContext(SCRIPT, {require:fakeRequire,URL,process});
'''.replace('SCRIPT', json.dumps(script))
    output = json.loads(subprocess.check_output(['node', '-e', runner], text=True))
    assert [r['connection_id'] for r in output['rows']] == ['ref']
    assert output['identity'] == 'connection_endpoint'


def test_endpoint_and_section_order(monkeypatch):
    import local_display_service as service
    from fastapi.testclient import TestClient
    from dashboard_page import DASHBOARD_HTML
    monkeypatch.setattr(ref, 'collect', lambda: {'status': 'unavailable', 'today': None})
    assert TestClient(service.app).get('/api/v1/refuseless').json()['today'] is None
    assert DASHBOARD_HTML.index('id="h-nord"') < DASHBOARD_HTML.index('id="h-refuseless"') < DASHBOARD_HTML.index('id="h-history"')


def test_empty_error_with_missing_usage_is_unknown_not_zero():
    missing = row(); missing.update(status=502, prompt=None, output=None, usage_provenance=None)
    result = ref.summarize({'identity': 'connection_endpoint', 'providers': {},
                            'rows': [missing]}, 'ok', '2026-11-23')
    assert result['period']['tokens'] is None
    assert result['today']['amount_usd'] is None
    assert result['daily'][0]['tokens'] is None
