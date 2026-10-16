import importlib.util
import json
import os as os
from pathlib import Path
import shutil
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('native_activate',
    Path(__file__).resolve().parents[1] / 'installers/macos/lib/pixel-native-activate.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def test_host_agent_continuation_cannot_be_used_as_initial_activation(monkeypatch):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    with pytest.raises(ValueError, match='host-agent-continuation-requires-recovery'):
        module.activate(preparation='/absent', install_dir='/absent', ods_source='/absent',
            compose_files=[], restore_host_agent=True)


@pytest.mark.parametrize('kwargs,code', [
    ({'inspect_continuation': True}, 'continuation-inspection-cannot-mutate'),
    ({'inspect_continuation': True, 'resume_final_health': True, 'configure_stack': True},
     'continuation-inspection-cannot-mutate'),
    ({'inspect_continuation': True, 'resume_final_health': True, 'restore_host_agent': True},
     'continuation-inspection-cannot-mutate'),
    ({'inspect_continuation': True, 'resume_final_health': True, 'resume_model': True},
     'continuation-inspection-cannot-mutate'),
    ({'opencode_choice': 'disabled'}, 'opencode-choice-requires-optional-operation'),
    ({'restore_optional_tools': True}, 'optional-tools-continuation-requires-recovery'),
    ({'inspect_continuation': True, 'resume_final_health': True, 'restore_optional_tools': True},
     'continuation-inspection-cannot-mutate'),
])
def test_inspection_rejects_mutations_before_loading_runtime(monkeypatch, kwargs, code):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    def forbidden(*args):
        pytest.fail('invalid inspection arguments must not load the runtime')
    monkeypatch.setattr(module, 'helper', forbidden)
    with pytest.raises(ValueError, match=code):
        module.activate(preparation='/absent', install_dir='/absent', ods_source='/absent',
            compose_files=[], **kwargs)


@pytest.mark.parametrize('fault', [None, 'no-webui', 'configure', 'environment', 'keys', 'files', 'config', 'existing',
    'prerequisites', 'infrastructure', 'protected', 'health', 'webui-routing', 'unsafe-recipe', 'recipe-alias', 'resume', 'resume-host', 'resume-model', 'resume-optional', 'inspect'])
def test_prepared_activation_validates_and_orders_real_entry_points(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(module.sys, 'platform', 'darwin')
    monkeypatch.setattr(module.os, 'geteuid', lambda: 501)
    owner = SimpleNamespace(pw_name='fixture', pw_uid=501, pw_dir=str(tmp_path))
    monkeypatch.setattr(module.pwd, 'getpwuid', lambda uid: owner)
    preparation, install_dir, home = [tmp_path / name for name in ('prepared', 'ods', 'native-home')]
    if fault in ('resume', 'resume-host', 'resume-model', 'resume-optional', 'inspect'):
        preparation = install_dir / 'data/pixel-native/preparation'
        home = install_dir / 'data/pixel-native/home'
    for path in (preparation, install_dir, home / '.openclaw'): path.mkdir(parents=True, exist_ok=True)
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
    recipe = install_dir / 'data/user-extensions/example/compose.yaml'
    recipe.parent.mkdir(parents=True)
    recipe.write_text(json.dumps({'services': {'example': {'image': 'example/app:1',
        **({'privileged': True} if fault in ('unsafe-recipe', 'recipe-alias') else {})}}}))
    if fault == 'recipe-alias':
        target = install_dir / 'unreviewed.yaml'
        recipe.rename(target)
        recipe.symlink_to(target)
    (install_dir / 'scripts').mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / 'scripts/resolve-compose-stack.sh',
        install_dir / 'scripts/resolve-compose-stack.sh')
    files.append(recipe)
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
            wait_ready=lambda run: event('health'),
            validate_stack=module.helper('pixel-native-compose.py').validate_stack),
    }
    if fault in ('resume', 'resume-host', 'resume-model', 'resume-optional'):
        def finish(**kwargs):
            events.append('recover')
            assert kwargs['receipt'] == receipt
            assert kwargs['preparation'] == preparation
            assert kwargs['verify']() == {'status': 'active', 'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}
            if fault == 'resume-host':
                kwargs['restore_host_agent']()
            else:
                assert 'restore_host_agent' not in kwargs
            if fault == 'resume-model':
                return {'selection': str(preparation / 'selection-update.json'),
                        'modelUpgrade': kwargs['resume_model'](lambda: events.append('unchanged'))}
            if fault == 'resume-optional':
                return {'selection': str(preparation / 'selection-update.json'),
                        'optionalTools': kwargs['restore_optional_tools'](lambda: events.append('unchanged'))}
            return preparation / 'selection-update.json'
        modules['pixel-native-recover.py'] = SimpleNamespace(finish=finish)
        def restore(path, environment):
            assert path == install_dir
            assert environment == dict(HOME=str(tmp_path), PATH='/usr/bin:/bin',
                DOCKER_HOST='unix:///socket', DOCKER_CONFIG=str(home / 'docker-config'))
            events.append('host-agent')
        def resume_model(path, selected_files, environment, *, verify_selection):
            assert path == install_dir and selected_files == files
            assert environment['DOCKER_HOST'] == 'unix:///socket'
            verify_selection()
            events.append('model-upgrade')
            return {'status': 'download-started', 'pid': 12345}
        modules['pixel-native-continuation.py'] = SimpleNamespace(
            restore_host_agent=restore, resume_model_upgrade=resume_model)
        if fault == 'resume-optional':
            def selected(path, services, *, opencode_choice):
                assert path == install_dir and opencode_choice == 'disabled'
                events.append('optional-selection')
                return {'opencode': {'selected': False}}, None
            def restore_optional(path, services, environment, *, opencode_choice, verify_selection, expected_snapshot):
                assert path == install_dir and environment['DOCKER_HOST'] == 'unix:///socket'
                assert expected_snapshot is None
                assert opencode_choice == 'disabled'
                verify_selection()
                events.append('optional-tools')
                return {'status': 'not-selected'}
            modules['pixel-native-continuation.py'] = SimpleNamespace(
                optional_setup_selection=selected, restore_optional_tools=restore_optional)
    if fault == 'inspect':
        def inspect(path, services, *, opencode_choice):
            assert path == install_dir
            assert services == {'dashboard-api': {}, 'model-router': {}, 'open-webui': {}}
            assert opencode_choice == 'disabled'
            events.append('inspect')
            return {'status': 'continuation-inspection', 'installerComplete': False}
        modules['pixel-native-continuation.py'] = SimpleNamespace(inspect_remaining_setup=inspect)
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
            if fault in ('resume', 'resume-host', 'resume-model', 'resume-optional'):
                events.append('verify-initial')
                assert '--verify-initial' in argv and '--install' not in argv
                assert '--initial-install' in argv
                assert kw['stdout'] == module.subprocess.PIPE and kw['timeout'] == 300
                return SimpleNamespace(returncode=0, stdout=json.dumps({
                    'status': 'active', 'runtimeDigest': 'a' * 64, 'serviceDigest': 'b' * 64}))
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
        if argv[-3:] == ['config', '--format', 'json']:
            events.append('services')
            services = {'dashboard-api': {}, 'model-router': {}}
            if fault != 'no-webui':
                services['open-webui'] = {}
            return SimpleNamespace(returncode=0, stdout=json.dumps({'services': services}))
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
    if fault in ('resume', 'resume-host', 'resume-model', 'resume-optional', 'inspect'): journal.write_text('retained failed attempt')
    def activate():
        return module.activate(preparation=preparation, install_dir=install_dir, ods_source=install_dir,
            compose_files=files, configure_stack=fault == 'configure',
            resume_final_health=fault in ('resume', 'resume-host', 'resume-model', 'resume-optional', 'inspect'),
            restore_host_agent=fault == 'resume-host', resume_model=fault == 'resume-model',
            inspect_continuation=fault == 'inspect',
            opencode_choice='disabled' if fault in ('inspect', 'resume-optional') else None,
            restore_optional_tools=fault == 'resume-optional')
    if fault == 'inspect':
        assert activate() == {'status': 'continuation-inspection', 'installerComplete': False}
        assert events == ['plan', 'bind', 'config', 'services', 'inspect']
        assert journal.read_text() == 'retained failed attempt'
        assert not (preparation / 'selection-update.json').exists()
        assert not (preparation / 'environment-before-native.env').exists()
        return
    if fault in ('resume', 'resume-host', 'resume-model', 'resume-optional'):
        expected_result = preparation / 'selection-update.json'
        if fault == 'resume-model':
            expected_result = {'selection': str(expected_result),
                'modelUpgrade': {'status': 'download-started', 'pid': 12345}}
        if fault == 'resume-optional':
            expected_result = {'selection': str(expected_result), 'optionalTools': {'status': 'not-selected'}}
        assert activate() == expected_result
        assert events == ['plan', 'bind', 'config', 'services'] + (
            ['optional-selection'] if fault == 'resume-optional' else []) + ['recover', 'verify-initial'] + (
            ['host-agent'] if fault == 'resume-host' else
            ['unchanged', 'services', 'optional-tools'] if fault == 'resume-optional' else
            ['unchanged', 'services', 'model-upgrade'] if fault == 'resume-model' else [])
        assert journal.read_text() == 'retained failed attempt'
        return
    if fault not in (None, 'no-webui', 'configure'):
        with pytest.raises(ValueError): activate()
    else:
        assert activate() == journal
        assert events == ['plan', 'bind'] + (['persist'] if fault == 'configure' else []) + [
            'config', 'services', 'prerequisites', 'infrastructure', 'protected', 'health'] + (
            [] if fault == 'no-webui' else ['webui-routing'])
        if fault == 'configure':
            assert (preparation / 'environment-before-native.env').read_text() == before
            assert env_path.read_text().startswith('LLM_MODEL=owner-selected\n')
    if fault in ('unsafe-recipe', 'recipe-alias'):
        assert events == ['plan', 'bind']
        assert not (preparation / 'environment-before-native.env').exists()
    if fault in ('environment', 'keys', 'files', 'config', 'unsafe-recipe', 'recipe-alias'):
        assert not journal.exists()
    elif fault == 'existing':
        assert journal.read_text() == 'do not overwrite'
    else:
        record = json.loads(journal.read_text())
        assert record['status'] == ('ready' if fault in (None, 'no-webui', 'configure') else 'error')
        assert 'private failure text' not in journal.read_text()
        assert journal.stat().st_mode & 0o777 == 0o600
        if fault not in (None, 'no-webui', 'configure'): assert record['requiresRecovery'] is True
