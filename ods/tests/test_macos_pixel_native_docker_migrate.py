import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('docker_migrate',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-docker-migrate.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('mode', ['plan', 'apply', 'handover-failure', 'release-failure', 'changed-env', 'changed-artifact'])
def test_owner_command_defaults_to_plan_and_recovers_only_unreleased_handover(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    monkeypatch.setenv('DOCKER_HOST', 'unix:///fixture/docker.sock')
    monkeypatch.setattr(module.Path, 'is_socket', lambda self: True)
    installed, prepared = tmp_path / 'installed', tmp_path / 'prepared'
    installed.mkdir()
    prepared.mkdir()
    env = b'PIXEL_NATIVE_WORKSPACE=/owner/workspace\nDASHBOARD_API_KEY=' + b'a' * 64 + b'\n'
    (installed / '.env').write_bytes(env)
    (installed / '.env').chmod(0o600)
    (installed / '.compose-flags').write_text('-f docker-compose.base.yml')
    (installed / 'docker-compose.base.yml').write_text('services: {}')
    receipt = {'kind': 'legacy-native', 'status': 'prepared', 'phase': 'awaiting-joint-activation',
        'environmentStatus': 'configured', 'installDir': str(installed),
        'environmentAfterSha256': hashlib.sha256(env).hexdigest(),
        'environmentBindings': {'PIXEL_NATIVE_WORKSPACE': '/owner/workspace'},
        'nativeTransport': {'docker': '/docker', 'project': 'ods', 'image': 'sha256:' + 'b' * 64, 'user': '501:20'},
        'legacyStorage': {'containers': {'ingress': 'c' * 64}}, 'runtimeDigest': 'd' * 64}
    for filename, field in (('storage.compose.json', 'storageDigest'), ('rollback.compose.json', 'rollbackDigest')):
        body = json.dumps({'volumes': {}}, sort_keys=True, separators=(',', ':')).encode()
        (prepared / filename).write_bytes(body)
        (prepared / filename).chmod(0o600)
        receipt[field] = hashlib.sha256(body).hexdigest()
    (prepared / 'preparation.json').write_text(json.dumps(receipt))
    (prepared / 'preparation.json').chmod(0o600)
    if mode == 'changed-env': (installed / '.env').write_bytes(env + b'OWNER_EDIT=1\n')
    if mode == 'changed-artifact': (prepared / 'rollback.compose.json').write_text('{}')
    commands, events = [], []
    def run(args, **kwargs):
        commands.append(args)
        assert args[-2:] == ['config', '--quiet']
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    original_helper = module.helper
    def handover(*args, **kwargs):
        events.append('handover')
        value = {'phase': 'infrastructure-ready-held', 'requiresRecovery': True}
        kwargs['checkpoint'](value)
        if mode == 'handover-failure': raise ValueError('injected-handover')
        return value
    def finish(*args, **kwargs):
        events.append('finish')
        kwargs['checkpoint']({'phase': 'releasing-admission', 'requiresRecovery': True})
        if mode == 'release-failure': raise ValueError('injected-release')
        kwargs['checkpoint']({'phase': 'infrastructure-ready', 'requiresRecovery': False})
    def restore(*args, **kwargs):
        events.append('restore')
        kwargs['checkpoint']({'phase': 'infrastructure-rolled-back', 'requiresRecovery': False})
    compose = SimpleNamespace(migrate_infrastructure=handover, finish_migration_infrastructure=finish,
        restore_migration_infrastructure=restore)
    monkeypatch.setattr(module, 'helper', lambda name: compose if name == 'compose' else original_helper(name))
    if mode in ('plan', 'apply'):
        result = module.migrate(prepared, apply=mode == 'apply')
        assert result['status'] == ('planned' if mode == 'plan' else 'docker-ready')
    else:
        with pytest.raises(ValueError): module.migrate(prepared, apply=True)
    if mode in ('plan', 'changed-env', 'changed-artifact'):
        assert not events and not (prepared / 'docker-migration.json').exists()
        if mode != 'plan': assert not commands
    else:
        assert events == (['handover', 'restore'] if mode == 'handover-failure' else ['handover', 'finish'])
        journal = prepared / 'docker-migration.json'
        assert journal.stat().st_mode & 0o777 == 0o600
        assert json.loads(journal.read_text())['requiresRecovery'] is (mode == 'release-failure')
        with pytest.raises(ValueError, match='needs-review'): module.migrate(prepared, apply=True)
