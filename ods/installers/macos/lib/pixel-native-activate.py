"""Activate an approved preparation against an already configured ODS stack.

The main installer supplies its Compose selection. --configure-stack can add
missing native environment bindings with a private backup. This entry point
does not infer model settings, accept a license, or overwrite an existing
preparation/activation attempt.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys


HERE = Path(__file__).resolve().parent


def helper(filename):
    spec = importlib.util.spec_from_file_location('native_activate_' + filename.replace('-', '_'), HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def activate(*, preparation, install_dir, ods_source, compose_files, configure_stack=False,
             resume_final_health=False, restore_host_agent=False, resume_model=False,
             inspect_continuation=False, opencode_choice=None, restore_optional_tools=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    if restore_host_agent and not resume_final_health:
        raise ValueError('host-agent-continuation-requires-recovery')
    if resume_model and not resume_final_health:
        raise ValueError('model-continuation-requires-recovery')
    if restore_optional_tools and not resume_final_health:
        raise ValueError('optional-tools-continuation-requires-recovery')
    if inspect_continuation and (not resume_final_health or configure_stack or restore_host_agent or resume_model or restore_optional_tools):
        raise ValueError('continuation-inspection-cannot-mutate')
    if opencode_choice is not None and not (inspect_continuation or restore_optional_tools):
        raise ValueError('opencode-choice-requires-optional-operation')
    config = helper('pixel-native-config.py')
    installer = helper('pixel-macos-access-install.py')
    compose = helper('pixel-native-compose.py')
    preparation, install_dir, ods_source = [Path(path).resolve(strict=True)
        for path in (preparation, install_dir, ods_source)]
    receipt = config.private_json(preparation / 'preparation.json')
    if (receipt.get('status') != 'prepared' or receipt.get('phase') != 'awaiting-protected-activation'
            or receipt.get('requiresActivation') is not True):
        raise ValueError('prepared-native-installation-required')
    home = Path(receipt['home'])
    template = Path(receipt['template'])
    if not home.is_absolute() or template != home / 'gateway.plist':
        raise ValueError('prepared-native-home-mismatch')
    document = config.private_json(home / '.openclaw/openclaw.json')
    owner = pwd.getpwuid(os.getuid())
    env = installer._env_file(install_dir / '.env')
    plan = installer.make_plan(install_dir=install_dir, owner_name=owner.pw_name,
        source_plist=template, openclaw_bin=home / 'openclaw', gateway_port=document['gateway']['port'],
        runtime_bundle=preparation / 'runtime', bundle_digest=receipt['runtimeDigest'], initial_install=True,
        access_port=int(env.get('PIXEL_NATIVE_ACCESS_PORT') or '18790'))
    installer.bind_initial_services(plan, bundle=preparation / 'services', digest=receipt['serviceDigest'],
        source_ref=receipt['pixelSourceRef'])
    source_env = plan['source_environment']
    keys = [env.get(name, '') for name in ('DASHBOARD_API_KEY', 'PIXEL_OPENWEBUI_KEY', 'PIXEL_MODEL_RELAY_KEY')]
    if len(set(keys)) != 3 or not all(re.fullmatch('[a-f0-9]{64}', key) for key in keys):
        raise ValueError('distinct-native-stack-credentials-required')
    expected = {
        'PIXEL_NATIVE_UID': str(owner.pw_uid),
        'PIXEL_INGRESS_GID': source_env['PIXEL_HISTORY_USER'].split(':')[1],
        'PIXEL_NATIVE_INGRESS_IMAGE': source_env['PIXEL_HISTORY_IMAGE'],
        'PIXEL_NATIVE_CONFIG_PATH': str(home / '.openclaw/openclaw.json'),
        'PIXEL_NATIVE_WORKSPACE': plan['gateway']['WorkingDirectory'],
        'PIXEL_NATIVE_GATEWAY_PORT': str(document['gateway']['port']),
        'PIXEL_NATIVE_ACCESS_PORT': str(plan['access_port']),
    }
    compose.validate_stack(install_dir, compose_files)
    paths = [Path(path).resolve(strict=True) for path in compose_files]
    required = [install_dir / relative for relative in (
        'extensions/services/pixel-model-relay/compose.yaml.disabled',
        'extensions/services/pixel-edge/compose.yaml.disabled',
        'installers/macos/pixel-native.compose.yaml.disabled')]
    if (len(paths) != len(set(paths)) or not all(path.is_file() for path in paths)
            or not all(path in paths for path in required) or paths.index(required[1]) > paths.index(required[2])):
        raise ValueError('complete-ordered-native-compose-stack-required')
    journal = preparation / 'activation.json'
    if os.path.lexists(journal) and not resume_final_health:
        raise ValueError('native-activation-journal-requires-review')
    if resume_final_health and (configure_stack or not os.path.lexists(journal)
            or preparation != install_dir / 'data/pixel-native/preparation'
            or home != install_dir / 'data/pixel-native/home'):
        raise ValueError('retained-initial-native-installation-required')
    compose.validate_stack(install_dir, paths)
    if configure_stack:
        bindings = {**expected,
            'PIXEL_INGRESS_RUNTIME_DIR': env.get('PIXEL_INGRESS_RUNTIME_DIR') or str(home / 'unused-docker-ingress'),
            'PIXEL_PREVIEW_RUNTIME_DIR': env.get('PIXEL_PREVIEW_RUNTIME_DIR') or str(home / 'unused-docker-preview')}
        helper('pixel-native-env.py').persist(install_dir / '.env', bindings,
            backup=preparation / 'environment-before-native.env')
        env = installer._env_file(install_dir / '.env')
    if any(env.get(key) != value for key, value in expected.items()):
        raise ValueError('persisted-native-compose-environment-mismatch')
    command = [source_env['PIXEL_HISTORY_DOCKER'], 'compose', '--project-directory', str(install_dir),
        '--project-name', source_env['PIXEL_HISTORY_PROJECT'], '--env-file', str(install_dir / '.env')]
    for path in paths:
        command.extend(['-f', str(path)])
    process_env = {'HOME': owner.pw_dir, 'PATH': source_env['PATH'],
        'DOCKER_HOST': source_env['DOCKER_HOST'], 'DOCKER_CONFIG': source_env['DOCKER_CONFIG']}
    if resume_final_health:
        process_env['ODS_PYTHON_CMD'] = helper('pixel-native-recovery-python.py').selected_python(install_dir)
    def run(*args, timeout=60, input=None):
        compose.validate_stack(install_dir, compose_files)
        compose.validate_stack(install_dir, paths)
        return subprocess.run([*command, *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, timeout=timeout, input=input)
    def docker_run(*args, timeout=60, input=None):
        return subprocess.run([source_env['PIXEL_HISTORY_DOCKER'], *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, timeout=timeout, input=input)
    # Validate Compose interpolation before recording or starting an attempt.
    if run('config', '--quiet').returncode:
        raise ValueError('native-compose-configuration-invalid')
    selected_stack = run('config', '--format', 'json')
    if selected_stack.returncode:
        raise ValueError('native-compose-configuration-invalid')
    selected_services = json.loads(selected_stack.stdout).get('services', {})
    if not isinstance(selected_services, dict):
        raise ValueError('native-compose-services-invalid')
    if inspect_continuation:
        return helper('pixel-native-continuation.py').inspect_remaining_setup(
            install_dir, selected_services, opencode_choice=opencode_choice)
    protected = ['/usr/bin/sudo', '/usr/bin/python3', str(HERE / 'pixel-macos-access-install.py'),
        '--source', str(ods_source), '--install-dir', str(install_dir), '--owner', owner.pw_name,
        '--gateway-plist', str(template), '--openclaw-bin', str(home / 'openclaw'),
        '--gateway-port', str(document['gateway']['port']), '--access-port', str(plan['access_port']),
        '--runtime-bundle', str(preparation / 'runtime'), '--bundle-digest', receipt['runtimeDigest'],
        '--services-bundle', str(preparation / 'services'), '--services-digest', receipt['serviceDigest'],
        '--pixel-source-ref', receipt['pixelSourceRef'], '--initial-install']
    if resume_final_health:
        def refresh_ingress(complete_held):
            nonlocal selected_services, optional_snapshot
            native_env = helper('pixel-native-env.py')
            if restore_optional_tools and native_env.snapshot(install_dir / '.env') != optional_snapshot:
                raise ValueError('retained-model-environment-changed')
            def operation(selection, record, checkpoint):
                nonlocal selected_services, optional_snapshot
                if restore_optional_tools:
                    after = native_env.snapshot(install_dir / '.env')
                    if hashlib.sha256(after[0]).hexdigest() != selection['environmentAfterSha256']:
                        raise ValueError('retained-model-environment-changed')
                    optional_snapshot = after
                current = run('config', '--format', 'json')
                if current.returncode:
                    raise ValueError('native-recovery-compose-selection-changed')
                latest = json.loads(current.stdout)['services']
                selected_services.clear()
                selected_services.update(latest)
                return complete_held()
            key = hashlib.sha256(json.dumps({'receipt': receipt,
                'source': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                'restoreHostAgent': restore_host_agent, 'restoreOptionalTools': restore_optional_tools,
                'opencodeChoice': opencode_choice}, sort_keys=True).encode()).hexdigest()
            refreshed = helper('pixel-native-ingress-refresh.py').refresh_locked(run, docker_run,
                install_dir=install_dir, project=source_env['PIXEL_HISTORY_PROJECT'], dashboard_key=keys[0],
                operation=operation, operation_kind='retained-recovery', operation_key=key)
            return refreshed['operationResult']
        def verify_selection(unchanged):
            unchanged()
            current = run('config', '--format', 'json')
            if current.returncode or json.loads(current.stdout).get('services') != selected_services:
                raise ValueError('native-recovery-compose-selection-changed')
        def verify():
            result = subprocess.run([*protected, '--verify-initial'], cwd='/',
                stdout=subprocess.PIPE, text=True, timeout=300, check=False)
            if result.returncode or len(result.stdout) > 4096:
                raise ValueError('native-initial-recovery-proof-failed')
            return json.loads(result.stdout)
        continuation = {}
        if restore_host_agent:
            continuation['restore_host_agent'] = lambda: helper('pixel-native-continuation.py').restore_host_agent(
                install_dir, process_env)
        if restore_optional_tools:
            optional, optional_snapshot = helper('pixel-native-continuation.py').optional_setup_selection(
                install_dir, selected_services, opencode_choice=opencode_choice)
            if optional['opencode']['selected'] is None:
                raise ValueError('retained-opencode-choice-required')
            continuation['restore_optional_tools'] = lambda unchanged: helper('pixel-native-continuation.py').restore_optional_tools(
                install_dir, selected_services, process_env, opencode_choice=opencode_choice,
                verify_selection=lambda: verify_selection(unchanged), expected_snapshot=optional_snapshot)
        if resume_model:
            def continue_model(unchanged):
                return helper('pixel-native-continuation.py').resume_model_upgrade(
                    install_dir, paths, process_env, verify_selection=lambda: verify_selection(unchanged))
            continuation['resume_model'] = continue_model
        return helper('pixel-native-recover.py').finish(preparation=preparation, receipt=receipt,
            run=run, verify=verify, compose=compose, selected_services=selected_services,
            refresh_ingress=refresh_ingress, **continuation)
    record = {'schemaVersion': 1, 'phase': 'infrastructure', 'status': 'activating',
        'runtimeDigest': receipt['runtimeDigest'], 'serviceDigest': receipt['serviceDigest']}
    def checkpoint():
        temporary = preparation / '.activation.json.tmp'
        with temporary.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write((json.dumps(record, sort_keys=True) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, journal)
    try:
        checkpoint()
        prerequisites = run('up', '-d', '--build', '--wait', '--wait-timeout', '120',
            'dashboard-api', 'pixel-model-relay', timeout=300)
        if prerequisites.returncode:
            raise ValueError('native-compose-prerequisites-failed')
        def config_checkpoint(value):
            record['ingressConfig'] = value
            checkpoint()
        record['ingressConfig'] = helper('pixel-native-ingress-config.py').prepare(run,
            docker_run=docker_run,
            environment_path=install_dir / '.env', backup=preparation / 'ingress-environment.before.env',
            checkpoint=config_checkpoint)
        checkpoint()
        compose.start_infrastructure(run, dashboard_key=keys[0])
        record['phase'] = 'protected-activation'
        checkpoint()
        result = subprocess.run([*protected, '--install'],
            cwd='/', timeout=900, check=False)
        if result.returncode:
            raise ValueError('native-protected-activation-failed')
        record['phase'] = 'final-health'
        checkpoint()
        compose.wait_ready(run)
        if 'open-webui' in selected_services:
            record['phase'] = 'webui-routing'
            checkpoint()
            if run('up', '-d', '--wait', '--wait-timeout', '120', 'open-webui', timeout=180).returncode:
                raise ValueError('native-webui-routing-failed')
        record.update(phase='services-ready', status='ready')
        checkpoint()
    except BaseException:
        record.update(status='error', requiresRecovery=True)
        checkpoint()
        raise
    return journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('preparation', 'install-dir', 'ods-source'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--compose-file', action='append', required=True)
    parser.add_argument('--configure-stack', action='store_true',
        help='Persist missing native Compose bindings, preserving existing values and a private backup')
    args = parser.parse_args()
    try:
        journal = activate(preparation=args.preparation, install_dir=args.install_dir,
            ods_source=args.ods_source, compose_files=args.compose_file, configure_stack=args.configure_stack)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError):
        print('Native activation failed; inspect its private phase journal. Do not reset or repeat automatically.', file=sys.stderr)
        return 1
    print(json.dumps({'activation': str(journal), 'status': 'services-ready'}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
