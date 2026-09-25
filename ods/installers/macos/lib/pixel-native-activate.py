"""Activate an approved preparation against an already configured ODS stack.

The main installer supplies its Compose selection. --configure-stack can add
missing native environment bindings with a private backup. This entry point
does not infer model settings, accept a license, or overwrite an existing
preparation/activation attempt.
"""
import argparse
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


def activate(*, preparation, install_dir, ods_source, compose_files, configure_stack=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
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
    paths = [Path(path).resolve(strict=True) for path in compose_files]
    required = [install_dir / relative for relative in (
        'extensions/services/pixel-model-relay/compose.yaml.disabled',
        'extensions/services/pixel-edge/compose.yaml.disabled',
        'installers/macos/pixel-native.compose.yaml.disabled')]
    if (len(paths) != len(set(paths)) or not all(path.is_file() for path in paths)
            or not all(path in paths for path in required) or paths.index(required[1]) > paths.index(required[2])):
        raise ValueError('complete-ordered-native-compose-stack-required')
    journal = preparation / 'activation.json'
    if os.path.lexists(journal):
        raise ValueError('native-activation-journal-requires-review')
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
    def run(*args, timeout=60, input=None):
        return subprocess.run([*command, *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, timeout=timeout, input=input)
    # Validate Compose interpolation before recording or starting an attempt.
    if run('config', '--quiet').returncode:
        raise ValueError('native-compose-configuration-invalid')
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
        compose.start_infrastructure(run, dashboard_key=keys[0])
        record['phase'] = 'protected-activation'
        checkpoint()
        result = subprocess.run(['/usr/bin/sudo', '/usr/bin/python3', str(HERE / 'pixel-macos-access-install.py'),
            '--source', str(ods_source), '--install-dir', str(install_dir), '--owner', owner.pw_name,
            '--gateway-plist', str(template), '--openclaw-bin', str(home / 'openclaw'),
            '--gateway-port', str(document['gateway']['port']), '--access-port', str(plan['access_port']),
            '--runtime-bundle', str(preparation / 'runtime'), '--bundle-digest', receipt['runtimeDigest'],
            '--services-bundle', str(preparation / 'services'), '--services-digest', receipt['serviceDigest'],
            '--pixel-source-ref', receipt['pixelSourceRef'], '--initial-install', '--install'],
            cwd='/', timeout=900, check=False)
        if result.returncode:
            raise ValueError('native-protected-activation-failed')
        record['phase'] = 'final-health'
        checkpoint()
        compose.wait_ready(run)
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
