"""Retained Hermes timeouts must follow the installed local template at boot."""
import os
from pathlib import Path
import stat

import pytest
import yaml

import hermes_config


TEMPLATE = 'providers:\n  custom:\n    request_timeout_seconds: 900\n'


@pytest.mark.parametrize('value', ['180', '180.0', '"180"', "'180'"])
def test_migrates_only_legacy_scalar_preserving_owner_bytes(value):
    original = ('# owner notes\nmodel:\n  api_key: "private-fixture"\n'
                'providers:\n  custom:\n    request_timeout_seconds: ' + value + ' # timeout\n'
                '    stale_timeout_seconds: 1200\n  other:\n    request_timeout_seconds: 180\n')
    replacement = value[0] + '900' + value[-1] if value[0] in "\"'" else '900'
    expected = original.replace('request_timeout_seconds: ' + value + ' #',
                                'request_timeout_seconds: ' + replacement + ' #')
    assert hermes_config.migrated_timeout(original, TEMPLATE) == expected
    assert hermes_config.migrated_timeout(expected, TEMPLATE) == expected


@pytest.mark.parametrize('text', [
    'model: {default: owner}\n',
    'providers: null\n',
    'providers: {custom: null}\n',
    'providers: {custom: {request_timeout_seconds: 600}}\n',
    'providers: {custom: {request_timeout_seconds: 900}}\n',
    'providers: {custom: {request_timeout_seconds: 1800}}\n',
    'providers: {custom: {request_timeout_seconds: &shared 180}, other: *shared}\n',
    'shared: &settings {request_timeout_seconds: 180}\nproviders: {custom: *settings}\n',
    'providers: {custom: &settings {request_timeout_seconds: 180}, other: *settings}\n',
    'providers: &p {custom: {request_timeout_seconds: 180}}\nother: *p\n',
    'providers: !!map &p {custom: {request_timeout_seconds: 180}}\nother: *p\n',
    'providers: {custom: !!map &c {request_timeout_seconds: 180}}\nother: *c\n',
])
def test_owner_overrides_and_shared_values_unchanged(text):
    assert hermes_config.migrated_timeout(text, TEMPLATE) == text


def test_cloud_template_does_not_change_retained_budget():
    live = TEMPLATE.replace('900', '180')
    assert hermes_config.migrated_timeout(live, live) == live


def test_flow_style_and_quoted_keys_are_migrated():
    text = '"providers": {"custom": {"request_timeout_seconds": 180}}\n'
    assert yaml.safe_load(hermes_config.migrated_timeout(text, TEMPLATE))['providers']['custom']['request_timeout_seconds'] == 900


@pytest.mark.parametrize('text', [
    'providers: {}\nproviders: {custom: {request_timeout_seconds: 180}}\n',
    'providers: {custom: {}, custom: {request_timeout_seconds: 180}}\n',
    'providers: {custom: {request_timeout_seconds: 180, request_timeout_seconds: 600}}\n',
    'providers: [bad]\n',
    'providers: {custom: [bad]}\n',
    'providers: [private-fixture',
])
def test_ambiguous_or_invalid_yaml_refused_without_echoing_content(text):
    with pytest.raises(ValueError) as error:
        hermes_config.migrated_timeout(text, TEMPLATE)
    assert 'private-fixture' not in str(error.value)


@pytest.fixture
def persisted(tmp_path):
    template = tmp_path / 'template.yaml'
    template.write_text(TEMPLATE)
    home = tmp_path / 'data'
    home.mkdir()
    config = home / 'config.yaml'
    config.write_text(TEMPLATE.replace('900', '180'))
    return home, template, config


@pytest.mark.skipif(os.name != 'posix', reason='Hermes startup runs inside a Linux container')
def test_private_config_atomic_migration_and_repeat_preserve_owner(persisted):
    home, template, config = persisted
    config.chmod(0o600)
    before = config.stat()
    assert hermes_config.migrate_provider_timeout(home, template)
    after = config.stat()
    assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (before.st_uid, before.st_gid, 0o600)
    assert config.read_text() == TEMPLATE
    assert not hermes_config.migrate_provider_timeout(home, template)
    assert config.stat().st_ino == after.st_ino
    assert list(home.iterdir()) == [config]


@pytest.mark.skipif(os.name != 'posix', reason='Hermes startup runs inside a Linux container')
@pytest.mark.parametrize('component', ['home', 'config'])
def test_symlinked_retained_data_refused(persisted, tmp_path, component):
    home, template, config = persisted
    original = config.read_bytes()
    if component == 'home':
        link = tmp_path / 'link'
        link.symlink_to(home, target_is_directory=True)
        home = link
    else:
        owner = home / 'owner.yaml'
        config.rename(owner)
        config.symlink_to(owner)
    with pytest.raises(OSError):
        hermes_config.migrate_provider_timeout(home, template)
    assert config.read_bytes() == original


@pytest.mark.skipif(os.name != 'posix', reason='Hermes startup runs inside a Linux container')
def test_fifo_refused_without_blocking(persisted):
    home, template, config = persisted
    config.unlink()
    os.mkfifo(config)
    with pytest.raises(ValueError, match='regular file'):
        hermes_config.migrate_provider_timeout(home, template)


@pytest.mark.skipif(os.name != 'posix', reason='Hermes startup runs inside a Linux container')
def test_owner_edit_detected_before_replace_is_not_overwritten(persisted, monkeypatch):
    home, template, config = persisted
    original_fsync = os.fsync
    replacement = home / 'owner-edit'
    replacement.write_text('owner: kept\n')
    def owner_edits(fd):
        original_fsync(fd)
        replacement.replace(config)
    monkeypatch.setattr(os, 'fsync', owner_edits)
    with pytest.raises(RuntimeError, match='changed during migration'):
        hermes_config.migrate_provider_timeout(home, template)
    assert config.read_text() == 'owner: kept\n'
    assert list(home.iterdir()) == [config]


@pytest.mark.skipif(os.name != 'posix', reason='Hermes startup runs inside a Linux container')
def test_directory_sync_failure_keeps_committed_config_and_no_temporary_file(persisted, monkeypatch):
    home, template, config = persisted
    real_fsync = os.fsync
    def sync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError('test directory sync failure')
        return real_fsync(fd)
    monkeypatch.setattr(os, 'fsync', sync)
    with pytest.raises(OSError):
        hermes_config.migrate_provider_timeout(home, template)
    assert config.read_text() == TEMPLATE
    assert list(home.iterdir()) == [config]


@pytest.mark.skipif(os.name != 'posix', reason='Hermes startup runs inside a Linux container')
def test_absent_retained_config_is_left_for_upstream_seed(persisted):
    home, template, config = persisted
    config.unlink()
    assert not hermes_config.migrate_provider_timeout(home, template)
    home.rmdir()
    assert not hermes_config.migrate_provider_timeout(home, template)


def test_compose_mounts_migration_at_bootstrap_path():
    compose = Path(__file__).resolve().parents[2] / 'hermes/compose.yaml'
    service = yaml.safe_load(compose.read_text())['services']['hermes']
    assert './extensions/services/dashboard-api/hermes_config.py:/opt/hermes/ods-config-migrate.py:ro,z' in service['volumes']


@pytest.mark.skipif(os.name != 'posix', reason='Hermes bootstrap runs in Linux')
@pytest.mark.parametrize('failure', [False, True])
def test_bootstrap_migrates_before_upstream_and_refuses_failed_migration(persisted, tmp_path, monkeypatch, failure):
    import subprocess
    import hermes_auth
    home, template, config = persisted
    script = tmp_path / 'migrator.py'
    script.write_text('# mounted migrator fixture\n')
    real_path = Path
    def bootstrap_path(value):
        return script if str(value) == '/opt/hermes/ods-config-migrate.py' else real_path(value)
    monkeypatch.setattr(hermes_auth, 'Path', bootstrap_path)
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setenv('HERMES_AUTH_POLICY_DIR', str(tmp_path / 'policy'))
    monkeypatch.setattr(os, 'getuid', lambda: 1000)
    called = []
    def migrate(args, check):
        assert args[-1] == str(script) and check is True
        called.append('migration')
        if failure:
            raise subprocess.CalledProcessError(1, args)
        hermes_config.migrate_provider_timeout(home, template)
    class UpstreamStarted(Exception):
        pass
    def start(*args):
        assert called == ['migration']
        assert config.read_text() == TEMPLATE
        raise UpstreamStarted
    monkeypatch.setattr(subprocess, 'run', migrate)
    monkeypatch.setattr(os, 'execv', start)
    with pytest.raises(subprocess.CalledProcessError if failure else UpstreamStarted):
        hermes_auth.bootstrap()
    assert called == ['migration']
    if failure:
        assert config.read_text() == TEMPLATE.replace('900', '180')
