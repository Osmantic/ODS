"""Opt-in real renderer/loader qualification; never installs or starts services.

Set ODS_TEST_NATIVE_CONFIG_LIVE=1 and ODS_TEST_PIXEL_SOURCE,
ODS_TEST_PIXEL_RUNTIME, ODS_TEST_PIXEL_NODE to prepared, licensed inputs.
"""
import importlib.util
import base64
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != 'darwin' or os.environ.get('ODS_TEST_NATIVE_CONFIG_LIVE') != '1',
    reason='requires an opted-in Mac and prepared licensed Pixel runtime')
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('qualification', ['config', 'bundle', 'layout', 'prepare', 'acquire', 'migration'])
def test_real_native_candidate_preserves_shared_ods_policy(tmp_path, qualification):
    migrating = qualification == 'migration'
    if migrating and os.environ.get('ODS_TEST_NATIVE_MIGRATION_LIVE') != '1':
        pytest.skip('reading an existing configuration requires explicit migration opt-in')
    package_runtime = qualification != 'config'
    if package_runtime and os.environ.get('ODS_TEST_NATIVE_BUNDLE_LIVE') != '1':
        pytest.skip('large runtime copy requires ODS_TEST_NATIVE_BUNDLE_LIVE=1')
    if qualification in ('layout', 'prepare', 'acquire') and os.environ.get('ODS_TEST_NATIVE_LAYOUT_LIVE') != '1':
        pytest.skip('layout proof requires explicit Docker binary/socket inputs')
    acquiring = qualification == 'acquire'
    if acquiring and os.environ.get('ODS_TEST_NATIVE_ACQUIRE_LIVE') != '1':
        pytest.skip('downloads and sandbox build require explicit acquisition opt-in')
    source = Path(os.environ['ODS_TEST_PIXEL_SOURCE']).resolve(strict=True)
    runtime = (tmp_path / 'Complete Preparation/acquired-runtime' if acquiring else
        Path(os.environ['ODS_TEST_PIXEL_RUNTIME']).resolve(strict=True))
    node = Path(os.environ['ODS_TEST_PIXEL_NODE']).resolve(strict=True)
    spec = importlib.util.spec_from_file_location('native_live_config',
        ROOT / 'installers/macos/lib/pixel-native-config.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ref = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=source, text=True).strip()
    module.bootstrap.selected_release(source, ref)
    key = tmp_path / 'fixture-key'
    key.write_text('qualification-only-not-a-live-credential')
    key.chmod(0o600)
    plugin = ROOT / 'extensions/services/pixel-agent/plugin'
    digest = module.bootstrap.command([str(node), str(source / 'scripts/hash-gateway-extension.mjs'),
                                       str(plugin)], cwd=tmp_path).strip()
    home = tmp_path / 'Owner Home'
    answers = tmp_path / 'onboarding.json'
    migration_args = {}
    if migrating:
        previous_path = Path(os.environ['ODS_TEST_PIXEL_PREVIOUS_CONFIG'])
        before_previous = previous_path.read_bytes()
        previous = module.private_json(previous_path)
        previous_workspace = next(agent for agent in previous['agents']['list'] if agent['id'] == 'pixel').get('workspace') or previous['agents']['defaults']['workspace']
        migration_args = {'previous_config': previous_path,
            'previous_state_dir': os.environ['ODS_TEST_PIXEL_PREVIOUS_STATE']}
        onboarding_spec = importlib.util.spec_from_file_location('native_live_migration_onboarding',
            ROOT / 'installers/macos/lib/pixel-native-onboarding.py')
        onboarding = importlib.util.module_from_spec(onboarding_spec)
        onboarding_spec.loader.exec_module(onboarding)
        environment = onboarding.helper('env')
        installed_env = Path(os.environ['ODS_TEST_INSTALLED_ODS']) / '.env'
        env_snapshot = environment.snapshot(installed_env)
        staged_env = tmp_path / 'migration.env'
        staged_env.write_bytes(env_snapshot[0])
        staged_env.chmod(0o600)
        backup = tmp_path / 'before-migration.env'
        changed = environment.ensure_credentials(staged_env, backup=backup, previous_config=previous_path)
        staged_values = {}
        for line in staged_env.read_text().splitlines():
            match = environment.ASSIGNMENT.fullmatch(line)
            if match:
                staged_values[match[1]] = environment.values.parse_env_value(match[2])
        if staged_values['PIXEL_MODEL_RELAY_KEY'] != previous['models']['providers']['ods-gateway']['apiKey']:
            pytest.fail('migration rotated the active relay credential')
        if changed:
            environment.restore(staged_env, backup=backup, expected=staged_env.read_bytes())
        if staged_env.read_bytes() != env_snapshot[0] or environment.snapshot(installed_env) != env_snapshot:
            pytest.fail('migration credential recovery changed the installed environment')
        onboarding.write(source=source, ref=ref, ods_source=ROOT,
            install_dir=os.environ['ODS_TEST_INSTALLED_ODS'], home=home, runtime=runtime,
            node=node, destination=answers, workspace=previous_workspace, previous_config=previous_path)
    elif not acquiring:
        module.bootstrap.command([sys.executable, str(ROOT / 'installers/lib/pixel-onboarding.py'),
            str(answers), str(runtime / 'node_modules/.bin/openclaw'), str(home),
            'Qwen3.5-9B', '16384', '4096', 'false', 'ods/current', 'Current',
            '4006', '18789', str(key), '8888', str(plugin), digest, 'searxng', '', ''], cwd=tmp_path)
    policy = tmp_path / 'operations-policy.json'
    if not migrating:
        policy.write_text(json.dumps({'schemaVersion': 1, 'targets': {}, 'actions': {}}))
        policy.chmod(0o600)
    # Rendering requires an immutable identity, not a running Docker sandbox.
    # This fixture does not claim image availability or execution qualification.
    image = 'sha256:' + 'a' * 64
    if migrating:
        image = module.bootstrap.prepare_sandbox(source=source, ref=ref,
            docker=os.environ['ODS_TEST_PIXEL_DOCKER'])['imageId']
        full_policy = module.private_json(policy)
        assert full_policy['targets']['ods-host']['writableRoots'] == [previous_workspace]
        assert 'host.os-release' in full_policy['actions']
    if qualification in ('prepare', 'acquire'):
        preparation_spec = importlib.util.spec_from_file_location('native_live_preparation',
            ROOT / 'installers/macos/lib/pixel-native-prepare.py')
        preparation = importlib.util.module_from_spec(preparation_spec)
        preparation_spec.loader.exec_module(preparation)
        output = tmp_path / 'Complete Preparation'
        if acquiring:
            installed = tmp_path / 'installed-ods'
            installed.mkdir(mode=0o700)
            (installed / '.env').write_text('GGUF_FILE=Qwen3.5-9B\nCTX_SIZE=16384\n')
            (installed / '.env').chmod(0o600)
            observer = installed / 'extensions/services/pixel-agent/host/system_observe.py'
            observer.parent.mkdir(parents=True)
            observer.write_bytes((ROOT / 'extensions/services/pixel-agent/host/system_observe.py').read_bytes())
            observer.chmod(0o600)
            assert not answers.exists()
        receipt_path = preparation.prepare(source=source, ref=ref, answers=None if acquiring else answers, node=node,
            install_dir=installed if acquiring else None, native_home=home if acquiring else None,
            runtime=None if acquiring else runtime, sandbox_image=None if acquiring else image, destination=output,
            npm=os.environ.get('ODS_TEST_PIXEL_NPM'), license_authorized=acquiring,
            docker=os.environ['ODS_TEST_PIXEL_DOCKER'],
            docker_socket=os.environ['ODS_TEST_PIXEL_DOCKER_SOCKET'], ods_source=ROOT,
            ingress_image='sha256:' + 'b' * 64, compose_project='ods-qualification', ingress_gid=os.getgid())
        receipt = json.loads(receipt_path.read_text())
        assert receipt['status'] == 'prepared' and receipt['requiresActivation'] is True
        assert receipt['phase'] == 'awaiting-protected-activation'
        if acquiring:
            credentials = preparation.helper('env')
            persisted = dict(line.split('=', 1) for line in (installed / '.env').read_text().splitlines())
            assert len({persisted[key] for key in credentials.CREDENTIALS}) == 3
            assert (output / 'environment-before-credentials.env').read_text() == 'GGUF_FILE=Qwen3.5-9B\nCTX_SIZE=16384\n'
            onboarding = json.loads((output / 'onboarding.json').read_text())
            assert onboarding['webSearchProvider'] == 'parallel-free'
            assert onboarding['modelName'] == 'ODS Current (Qwen3.5-9B)'
            assert onboarding['modelContextWindow'] == 16384 and onboarding['modelMaxTokens'] == 4096
            assert receipt['sandbox']['ownerUid'] == os.getuid()
            assert receipt['sandbox']['pixelSourceRef'] == ref
            generated = json.loads((output / 'candidate/openclaw.json').read_text())
            assert generated['agents']['defaults']['sandbox']['docker']['image'] == receipt['sandbox']['imageId']
        module.bundle.verify(output / 'runtime', expected_digest=receipt['runtimeDigest'])
        import hashlib
        module.verified_services(output / 'services', expected_digest=receipt['serviceDigest'],
            expected_ref=ref, expected_config_digest=hashlib.sha256((output / 'candidate/openclaw.json').read_bytes()).hexdigest())
        assert Path(receipt['template']).is_file()
        assert (home / '.openclaw/.ods-exec-control/cancellable-exec.sh').is_file()
        return
    candidate = module.prepare(source=source, ref=ref, answers=answers, node=node,
        sandbox_image=image, destination=tmp_path / 'candidate', runtime=runtime,
        research_port=3099, **migration_args)
    value = json.loads((candidate / 'openclaw.json').read_text())
    assert value['plugins']['entries']['pixel-ods']['config']['workspacePreviewTransport'] == 'docker-desktop'
    if qualification == 'config':
        # Re-render a managed configuration, not just the historical pixel-ods-only form.
        state = (tmp_path / 'managed-state').resolve()
        state.mkdir()
        managed_agent = next(item for item in value['agents']['list'] if item['id'] == 'pixel')
        workspace = Path(managed_agent.get('workspace') or value['agents']['defaults']['workspace'])
        workspace.mkdir(parents=True, exist_ok=True)
        before = (candidate / 'openclaw.json').read_bytes()
        updated = module.prepare(source=source, ref=ref, answers=answers, node=node,
            sandbox_image=image, destination=tmp_path / 'managed-update', runtime=runtime,
            research_port=3099, previous_config=candidate / 'openclaw.json', previous_state_dir=state)
        preserved = module.private_json(updated / 'openclaw.json')
        assert preserved['plugins']['allow'] == value['plugins']['allow']
        assert preserved['gateway']['auth'] == value['gateway']['auth']
        assert preserved['models']['providers'] == value['models']['providers']
        assert (candidate / 'openclaw.json').read_bytes() == before
        assert module.private_json(updated / 'migration.json')['stateDir'] == str(state)
    if migrating:
        if value['models']['providers']['ods-gateway'] != previous['models']['providers']['ods-gateway']:
            pytest.fail('migration changed the selected model provider')
        if value['gateway']['auth'] != previous['gateway']['auth']:
            pytest.fail('migration changed gateway authentication')
        record = module.private_json(candidate / 'migration.json')
        import hashlib
        assert record['candidateConfigSha256'] == hashlib.sha256((candidate / 'openclaw.json').read_bytes()).hexdigest()
        assert record['stateDir'] == os.environ['ODS_TEST_PIXEL_PREVIOUS_STATE']
        assert record['requiresJointActivation'] is True
        if previous_path.read_bytes() != before_previous:
            pytest.fail('migration staging changed the installed configuration')
    tools = set(value['tools']['alsoAllow'])
    assert {'pixel_ops_run', 'pixel_ods_workspace_preview', 'create_goal', 'cron'} <= tools
    sandbox = value['agents']['defaults']['sandbox']['docker']
    assert sandbox['image'] == image
    assert sandbox['binds'] == [str(home / '.openclaw/.ods-exec-control') + ':/run/pixel-ods-control:ro']
    assert value['plugins']['entries']['pixel-ods']['config']['perplexicaPort'] == 3099
    agent = next(agent for agent in value['agents']['list'] if agent['id'] == 'pixel')
    assert agent['experimental']['localModelLean'] is False
    expected_context = (next(model for model in previous['models']['providers']['ods-gateway']['models']
        if model['id'] == 'ods/current')['contextWindow'] if migrating else 16384)
    assert agent['contextLimits']['toolResultMaxChars'] == max(4000, min(16000, expected_context // 4))
    assert agent['model'] == 'ods-gateway/ods/current'
    assert (candidate / 'workspace/AGENTS.md').is_file()
    assert not (home / '.openclaw/openclaw.json').exists()
    assert json.loads((candidate / 'candidate.json').read_text())['requiresServiceQualification'] is True
    services = tmp_path / 'Native Service Bundle'
    service_digest = module.stage_services(source=source, ref=ref, ods_source=ROOT,
        candidate=candidate, destination=services)
    import hashlib
    service_manifest = json.loads((services / 'services.json').read_bytes())
    assert hashlib.sha256((services / 'services.json').read_bytes()).hexdigest() == service_digest
    assert service_manifest['pixelSourceRef'] == ref
    assert service_manifest['requiresServiceQualification'] is True
    assert (services / 'operations/broker.py').read_bytes() == (source / 'deploy/ops-broker/broker.py').read_bytes()
    assert (services / 'operations/policy.json').read_bytes() == (candidate / 'operations-policy.json').read_bytes()
    for name, record in service_manifest['files'].items():
        body = (services / name).read_bytes()
        assert hashlib.sha256(body).hexdigest() == record['sha256']
        assert len(body) == record['bytes']
        assert (services / name).stat().st_mode & 0o777 == 0o600
    assert not (services / 'openclaw.json').exists()
    catalog = json.loads((services / 'helpers/extension-catalog.json').read_bytes())
    assert catalog['kind'] == 'ods-pixel-extension-catalog'
    assert catalog['extensions']
    assert len(catalog['sourceSha256']) == 64
    approved_services = module.verified_services(services, expected_digest=service_digest,
        expected_ref=ref, expected_config_digest=hashlib.sha256((candidate / 'openclaw.json').read_bytes()).hexdigest())
    assert approved_services['operations/broker.py'] == (source / 'deploy/ops-broker/broker.py').read_bytes()
    if package_runtime:
        import pwd
        before = (candidate / 'openclaw.json').read_bytes()
        destination = tmp_path / 'Complete Runtime Bundle'
        digest = module.stage_bundle(source=source, ref=ref, candidate=candidate,
                                     node=node, runtime=runtime, destination=destination)
        manifest, actual = module.bundle.verify(destination, expected_digest=digest)
        assert actual == digest
        assert len(manifest['plugins']) == len(value['plugins']['load']['paths'])
        assert (candidate / 'openclaw.json').read_bytes() == before
        assert not list(tmp_path.glob('.pixel-package-*'))
        installer_spec = importlib.util.spec_from_file_location('native_live_installer',
            ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
        installer = importlib.util.module_from_spec(installer_spec)
        installer_spec.loader.exec_module(installer)
        plan = installer._bundle_plan(destination, digest,
            {'OPENCLAW_CONFIG_PATH': str(candidate / 'openclaw.json')}, pwd.getpwuid(os.getuid()))
        mapped = json.loads(plan['config_bytes'])
        assert mapped['plugins']['load']['paths'] == [
            str(installer._bundle.INSTALL_ROOT / digest / relative) for relative in manifest['plugins']]
        mapped['plugins']['load']['paths'] = value['plugins']['load']['paths']
        assert mapped == value
        if migrating:
            plan = installer.make_migration_plan(owner_name=pwd.getpwuid(os.getuid()).pw_name,
                install_dir=os.environ['ODS_TEST_INSTALLED_ODS'], openclaw_bin=installer.GATEWAY_LAUNCHER,
                current_digest=os.environ['ODS_TEST_PIXEL_CURRENT_DIGEST'], gateway_port=value['gateway']['port'],
                candidate=candidate, runtime_bundle=destination, bundle_digest=digest,
                services_bundle=services, services_digest=service_digest, source_ref=ref)
            assert plan['runtime_bundle']['digest'] == digest
            assert plan['migration_qualification']['preservation']['stateDir'] == os.environ['ODS_TEST_PIXEL_PREVIOUS_STATE']
            assert plan['native_services']['expected_digest'] == service_digest
            gateway_env, _ = installer._env_assignments(plan['gateway']['ProgramArguments'])
            assert gateway_env['OPENCLAW_STATE_DIR'] == os.environ['ODS_TEST_PIXEL_PREVIOUS_STATE']
            assert gateway_env['OPENCLAW_CONFIG_PATH'].endswith('openclaw-' + digest + '.json')
            assert gateway_env['PIXEL_OPS_STATE_DIR'] == '/private/var/lib/pixel-ops-broker'
            assert not Path(plan['runtime_bundle']['config_path']).exists()
            recovered = installer._decode_recovery_context(installer._recovery_context(plan),
                current_digest=os.environ['ODS_TEST_PIXEL_CURRENT_DIGEST'], candidate_digest=digest,
                owner_name=pwd.getpwuid(os.getuid()).pw_name)
            installer._verify_recovery_runtime(recovered)
            if recovered['migration_source_config_bytes'] != before_previous:
                pytest.fail('migration recovery lost the original configuration bytes')
            if previous_path.read_bytes() != before_previous:
                pytest.fail('migration planning modified the active configuration')
        if qualification == 'layout':
            layout_spec = importlib.util.spec_from_file_location('native_live_layout',
                ROOT / 'installers/macos/lib/pixel-native-layout.py')
            layout = importlib.util.module_from_spec(layout_spec)
            layout_spec.loader.exec_module(layout)
            template = layout.prepare(candidate=candidate, home=home, node=node, runtime=runtime,
                docker=os.environ['ODS_TEST_PIXEL_DOCKER'],
                docker_socket=os.environ['ODS_TEST_PIXEL_DOCKER_SOCKET'], ods_source=ROOT,
                ingress_image='sha256:' + 'b' * 64, compose_project='ods-qualification', ingress_gid=os.getgid())
            install_dir = tmp_path / 'ods-install'
            install_dir.mkdir(mode=0o700)
            (install_dir / '.env').write_text('DASHBOARD_API_KEY=' + 'd' * 64 + '\nPIXEL_OPENWEBUI_KEY=' + 'e' * 64 + '\n')
            (install_dir / '.env').chmod(0o600)
            cold = installer.make_plan(install_dir=install_dir, owner_name=pwd.getpwuid(os.getuid()).pw_name,
                source_plist=template, openclaw_bin=home / 'openclaw', gateway_port=18789,
                runtime_bundle=destination, bundle_digest=digest, initial_install=True)
            assert cold['initial_install'] is True
            service_bundle = tmp_path / 'services-for-initial-install'
            service_digest = module.stage_services(source=source, ref=ref, ods_source=ROOT,
                candidate=candidate, destination=service_bundle)
            installer.bind_initial_services(cold, bundle=service_bundle, digest=service_digest, source_ref=ref)
            assert cold['native_services']['expected_digest'] == service_digest
            assert cold['runtime_bundle']['digest'] == digest
            assert cold['gateway']['UserName'] == pwd.getpwuid(os.getuid()).pw_name
            assert cold['gateway']['WorkingDirectory'] == str(home / '.openclaw/workspace-pixel')
            assert cold['source_environment']['PIXEL_HISTORY_TRANSPORT'] == 'docker-exec'
            assert cold['source_environment']['PIXEL_PREVIEW_DOCKER'] == str(Path(os.environ['ODS_TEST_PIXEL_DOCKER']).resolve())
            assert set(cold['policies']) == {'sandboxed', 'full-access'}
            control = home / '.openclaw/.ods-exec-control'
            workspace = home / '.openclaw/workspace-pixel'
            fixture_env = {'HOME': str(home), 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}
            module.bootstrap.command([str(control / 'cancellable-exec.sh'), 'a' * 64,
                base64.b64encode(b"/usr/bin/touch 'native layout proof.txt'").decode()],
                cwd=workspace, env=fixture_env)
            assert (workspace / 'native layout proof.txt').is_file()
            (control / ('b' * 64 + '.cancel')).touch()
            cancelled = subprocess.run([str(control / 'cancellable-exec.sh'), 'b' * 64,
                base64.b64encode(b'/bin/sleep 30').decode()], cwd=workspace, env=fixture_env,
                capture_output=True, text=True, timeout=10)
            assert cancelled.returncode == 130
