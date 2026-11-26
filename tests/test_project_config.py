"""Unified project dotenv configuration, with no live config or service reads."""
from pathlib import Path
import json

import pytest

import auto_usage
import opencodex_collect as collector
import opencodex_usage as usage
import project_config


def test_shared_loader_preserves_existing_syntax_and_environment_precedence(tmp_path, monkeypatch):
    example = tmp_path / '.env'
    example.write_text('# comment\nFAKE_EXISTING=from-file\nFAKE_NEW=a=b\nFAKE_EMPTY=\nno-equals\n')
    monkeypatch.setenv('FAKE_EXISTING','from-process')
    monkeypatch.delenv('FAKE_NEW',raising=False)
    monkeypatch.delenv('FAKE_EMPTY',raising=False)
    project_config.load_env(example)
    assert project_config.os.environ['FAKE_EXISTING'] == 'from-process'
    assert project_config.os.environ['FAKE_NEW'] == 'a=b'
    assert project_config.os.environ['FAKE_EMPTY'] == ''
    monkeypatch.setattr(auto_usage,'SCRIPT_DIR',str(tmp_path))
    auto_usage.load_env()
    assert project_config.os.environ['FAKE_EXISTING'] == 'from-process'


def test_configured_api_reads_shared_project_settings_without_cli_url(tmp_path, monkeypatch):
    from test_opencodex_transport import fixture_server, serve_json
    raw = json.loads((Path(__file__).parent/'fixtures/opencodex-usage-v1.json').read_text())['windows'][0]['response']
    example = tmp_path / '.env'
    cache = tmp_path / 'cache.sqlite'
    real_loader = project_config.load_env
    for key in ('OPENCODEX_USAGE_ENABLED','OPENCODEX_API_BASE_URL','OPENCODEX_USAGE_DB','OPENCODEX_USAGE_SOURCE_ID'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(project_config,'load_env',lambda:real_loader(example))
    with fixture_server(lambda h:serve_json(h,raw)) as (url, requests):
        example.write_text('OPENCODEX_USAGE_ENABLED=1\nOPENCODEX_API_BASE_URL='+url+'\n'
                           'OPENCODEX_USAGE_SOURCE_ID=fixture-mac\nOPENCODEX_USAGE_DB='+str(cache)+'\n')
        assert collector.main(['--days','1','--end-date','2026-11-23']) == 0
    assert len(requests) == 1 and usage.view(1)['daily'][0]['total_tokens'] == 300
    assert b'pool-demo' not in cache.read_bytes()


def test_file_fallback_does_not_contact_configured_api(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED','1')
    monkeypatch.setenv('OPENCODEX_API_BASE_URL','http://127.0.0.1:12345')
    def forbidden(*args, **kwargs):
        raise AssertionError('File import must never open a transport')
    monkeypatch.setattr(collector,'_fetch',forbidden)
    result = collector.collect(input_path=Path(__file__).parent/'fixtures/opencodex-aggregate-v2.json',
                               identity='fixture-mac', path=tmp_path/'owned.sqlite')
    assert result['days_changed'] == 1


def test_disabled_config_never_contacts_api_or_creates_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED','0')
    monkeypatch.setenv('OPENCODEX_API_BASE_URL','http://127.0.0.1:12345')
    def forbidden(*args, **kwargs):
        raise AssertionError('Disabled source must not open a transport')
    monkeypatch.setattr(collector,'_fetch',forbidden)
    target = tmp_path/'absent.sqlite'
    with pytest.raises(usage.InvalidSnapshot):
        collector.collect(path=target)
    assert not target.exists()


def test_nonlocal_or_credentialed_config_is_refused_before_transport(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCODEX_USAGE_ENABLED','1')
    monkeypatch.setenv('OPENCODEX_API_BASE_URL','http://token@example.invalid:12345')
    monkeypatch.setattr(collector,'_fetch',lambda *a,**k:pytest.fail('Unsafe transport opened'))
    with pytest.raises(usage.InvalidSnapshot):
        collector.collect(path=tmp_path/'absent.sqlite')
    assert not (tmp_path/'absent.sqlite').exists()
