"""Check setup preservation without launching a Solr runtime."""
import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def setup(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[4] / 'extensions/library/services/solr/security.py'
    spec = importlib.util.spec_from_file_location('solr_preflight', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / 'data'
    data.mkdir()
    health = tmp_path / 'health'
    monkeypatch.setattr(module, 'Path', lambda value: data / 'security.json' if value.endswith('security.json') else health)
    monkeypatch.setattr(module.os, 'umask', lambda value: None)
    monkeypatch.setenv('SOLR_ADMIN_PASSWORD', 'a' * 64)
    return module, data / 'security.json', health


def test_initial_security_is_authenticated_and_persisted_without_plaintext(setup):
    module, target, health = setup
    module.configure()
    policy = json.loads(target.read_text())
    assert policy['authentication']['blockUnknown'] is True
    assert policy['authorization']['user-role'] == {'ods': 'admin'}
    assert 'a' * 64 not in target.read_text()
    assert health.read_text() == 'user = "ods:' + 'a' * 64 + '"\n'


def test_restart_retains_native_policy_and_other_users(setup):
    module, target, _ = setup
    module.configure()
    policy = json.loads(target.read_text())
    policy['authentication']['credentials']['project'] = 'retained'
    policy['authorization']['user-role']['project'] = 'reader'
    target.write_text(json.dumps(policy))
    before = target.read_bytes()
    module.configure()
    assert target.read_bytes() == before


def test_mismatched_password_preserves_existing_security(setup, monkeypatch):
    module, target, _ = setup
    module.configure()
    before = target.read_bytes()
    monkeypatch.setenv('SOLR_ADMIN_PASSWORD', 'b' * 64)
    with pytest.raises(SystemExit, match='preserved'):
        module.configure()
    assert target.read_bytes() == before


def test_invalid_bootstrap_secret_does_not_create_policy(setup, monkeypatch):
    module, target, health = setup
    monkeypatch.setenv('SOLR_ADMIN_PASSWORD', 'short')
    with pytest.raises(SystemExit, match='64 lowercase'):
        module.configure()
    assert not target.exists()
    assert not health.exists()


def test_changed_authentication_plugin_is_not_overwritten(setup):
    module, target, _ = setup
    target.write_text('{"authentication":{"class":"other.Plugin"}}')
    before = target.read_bytes()
    with pytest.raises(SystemExit, match='reconciliation'):
        module.configure()
    assert target.read_bytes() == before
