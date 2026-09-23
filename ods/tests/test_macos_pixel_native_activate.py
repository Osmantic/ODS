import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_activate',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-activate.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('fault', [None, 'configure', 'environment', 'keys', 'files', 'config', 'existing',
    'prerequisites', 'infrastructure', 'protected', 'health', 'webui-routing'])
def test_prepared_activation_validates_and_orders_real_entry_points(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    owner = SimpleNamespace(pw_name='fixture', pw_uid=501, pw_dir=str(tmp_path))
    monkeypatch.setattr(module.pwd, 'getpwuid', lambda uid: owner)
    preparation, install_dir, home = [tmp_path / name for name in ('prepared', 'ods', 'native-home')]
    for path in (preparation, install_dir, home / '.openclaw'): path.mkdir(parents=True)
    receipt = {'status': 'prepared', 'phase': 'awaiting-protected-activation', 'requiresActivation': True,
        'home': str(home), 'template': str(home / 'gateway.plist'), 'runtimeDigest': 'a' * 64,
        'serviceDigest': 'b' * 64, 'pixelSourceRef': 'c' * 40}
    (preparation / 'preparation.json').write_text(json.dumps(receipt))
    (home / '.openclaw/openclaw.json').write_text(json.dumps({'gateway': {'port': 18789}}))
    files = []
    for relative in ('docker-compose.base.yml', 'extensions/services/pixel-model-relay/compose.yaml.disabled',
                     'extensions/services/pixel-edge/compose.yaml.disabled', 'installers/macos/pixel-native.compose.yaml.disabled'):
        path = install_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('fixture')
        files.append(path)
    if fault == 'files': files.reverse()
    env = dict(DASHBOARD_API_KEY='d' * 64, PIXEL_OPENWEBUI_KEY='e' * 64, PIXEL_MODEL_RELAY_KEY='f' * 64,
        PIXEL_NATIVE_UID='501', PIXEL_INGRESS_GID='20', PIXEL_NATIVE_INGRESS_IMAGE='sha256:' + 'a' * 64,
        PIXEL_NATIVE_CONFIG_PATH=str(home / '.openclaw/openclaw.json'), PIXEL_NATIVE_WORKSPACE=str(home / 'workspace'),
        PIXEL_NATIVE_GATEWAY_PORT='18789', PIXEL_NATIVE_ACCESS_PORT='18795')
    if fault == 'environment': env['PIXEL_NATIVE_UID'] = '500'
    if fault == 'keys': env['PIXEL_OPENWEBUI_KEY'] = env['DASHBOARD_API_KEY']
    plan = {'gateway': {'WorkingDirectory': str(home / 'workspace')}, 'access_port': 18795,
        'source_environment': {'PIXEL_HISTORY_USER': '501:20', 'PIXEL_HISTORY_IMAGE': 'sha256:' + 'a' * 64,
            'PIXEL_HISTORY_DOCKER': '/docker', 'PIXEL_HISTORY_PROJECT': 'ods', 'PATH': '/usr/bin:/bin',
            'DOCKER_HOST': 'unix:///socket', 'DOCKER_CONFIG': str(home / 'docker-config')}}
    events = []
    def event(name):
        events.append(name)
        if fault == name: raise ValueError('private failure text')
    def make_plan(**kw):
        event('plan')
        assert kw['initial_install'] is True and kw['access_port'] == 18795
        return plan
    def bind(p, **kw):
        event('bind')
        assert p is plan and kw['source_ref'] == receipt['pixelSourceRef']
    modules = {
        'pixel-native-config.py': SimpleNamespace(private_json=lambda path: json.loads(Path(path).read_text())),
        'pixel-macos-access-install.py': SimpleNamespace(make_plan=make_plan, bind_initial_services=bind,
            _env_file=lambda path: env),
        'pixel-native-compose.py': SimpleNamespace(start_infrastructure=lambda run, **kw: event('infrastructure'),
            wait_ready=lambda run: event('health')),
    }
    if fault == 'configure':
        native_env = module.helper('pixel-native-env.py')
        for key in list(env):
            if key.startswith('PIXEL_NATIVE_') and key != 'PIXEL_NATIVE_ACCESS_PORT': del env[key]
        env_path = install_dir / '.env'
        before = 'LLM_MODEL=owner-selected\n' + ''.join(key + '=' + value + '\n' for key, value in env.items())
        env_path.write_text(before)
        env_path.chmod(0o600)
        def persist(path, bindings, **kw):
            event('persist')
            native_env.persist(path, bindings, **kw)
            env.update(bindings)
        modules['pixel-native-env.py'] = SimpleNamespace(persist=persist)
    monkeypatch.setattr(module, 'helper', modules.__getitem__)
    def run(argv, **kw):
        assert all(key not in ' '.join(argv) for key in (env['DASHBOARD_API_KEY'], env['PIXEL_OPENWEBUI_KEY']))
        if argv[0] == '/usr/bin/sudo':
            events.append('protected')
            assert argv[1] == '/usr/bin/python3'
            assert '--initial-install' in argv and '--install' in argv
            assert argv[argv.index('--services-digest') + 1] == receipt['serviceDigest']
            assert argv[argv.index('--access-port') + 1] == '18795'
            return SimpleNamespace(returncode=1 if fault == 'protected' else 0)
        assert argv[:2] == ['/docker', 'compose']
        assert kw['env']['DOCKER_HOST'] == 'unix:///socket'
        if argv[-2:] == ['config', '--quiet']:
            events.append('config')
            return SimpleNamespace(returncode=1 if fault == 'config' else 0)
        if argv[-1] == 'open-webui':
            events.append('webui-routing')
            assert '--wait' in argv
            return SimpleNamespace(returncode=1 if fault == 'webui-routing' else 0)
        assert argv[-2:] == ['dashboard-api', 'pixel-model-relay']
        assert '--wait' in argv
        events.append('prerequisites')
        return SimpleNamespace(returncode=1 if fault == 'prerequisites' else 0)
    monkeypatch.setattr(module.subprocess, 'run', run)
    journal = preparation / 'activation.json'
    if fault == 'existing': journal.write_text('do not overwrite')
    def activate():
        return module.activate(preparation=preparation, install_dir=install_dir, ods_source=install_dir,
            compose_files=files, configure_stack=fault == 'configure')
    if fault not in (None, 'configure'):
        with pytest.raises(ValueError): activate()
    else:
        assert activate() == journal
        assert events == ['plan', 'bind'] + (['persist'] if fault == 'configure' else []) + [
            'config', 'prerequisites', 'infrastructure', 'protected', 'health', 'webui-routing']
        if fault == 'configure':
            assert (preparation / 'environment-before-native.env').read_text() == before
            assert env_path.read_text().startswith('LLM_MODEL=owner-selected\n')
    if fault in ('environment', 'keys', 'files', 'config'):
        assert not journal.exists()
    elif fault == 'existing':
        assert journal.read_text() == 'do not overwrite'
    else:
        record = json.loads(journal.read_text())
        assert record['status'] == ('ready' if fault in (None, 'configure') else 'error')
        assert 'private failure text' not in journal.read_text()
        assert journal.stat().st_mode & 0o777 == 0o600
        if fault not in (None, 'configure'): assert record['requiresRecovery'] is True
