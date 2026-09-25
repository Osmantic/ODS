"""Apply a prepared legacy Docker handover without changing the native runtime."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def helper(name):
    spec = importlib.util.spec_from_file_location('docker_migrate_' + name,
        Path(__file__).with_name('pixel-native-' + name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def migrate(preparation, *, apply=False):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    preparation = Path(preparation).resolve(strict=True)
    environment, compose = helper('env'), helper('compose')
    receipt = json.loads(environment.snapshot(preparation / 'preparation.json')[0])
    if (receipt.get('kind') != 'legacy-native' or receipt.get('status') != 'prepared'
            or receipt.get('phase') != 'awaiting-joint-activation'
            or receipt.get('environmentStatus') != 'configured'):
        raise ValueError('configured-legacy-preparation-required')
    journal = preparation / 'docker-migration.json'
    if os.path.lexists(journal):
        raise ValueError('existing-docker-migration-needs-review')
    install_dir = Path(receipt['installDir']).resolve(strict=True)
    documents = {}
    for filename, field in (('storage.compose.json', 'storageDigest'), ('rollback.compose.json', 'rollbackDigest')):
        body = environment.snapshot(preparation / filename)[0]
        document = json.loads(body)
        if hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':')).encode()).hexdigest() != receipt[field]:
            raise ValueError('prepared-docker-artifact-changed')
        documents[filename] = document
    current_env = environment.snapshot(install_dir / '.env')[0]
    if hashlib.sha256(current_env).hexdigest() != receipt.get('environmentAfterSha256'):
        raise ValueError('prepared-native-environment-changed')
    values = {}
    for line in current_env.decode().splitlines():
        match = environment.ASSIGNMENT.fullmatch(line)
        if match:
            values[match[1]] = environment.values.parse_env_value(match[2])
    if any(values.get(key) != value for key, value in receipt['environmentBindings'].items()):
        raise ValueError('prepared-native-bindings-changed')
    transport = receipt['nativeTransport']
    docker, project = transport['docker'], transport['project']
    process_env = dict(os.environ)
    endpoint = process_env.get('DOCKER_HOST')
    if not endpoint:
        result = subprocess.run([docker, 'context', 'inspect'], capture_output=True, text=True, timeout=15, check=True)
        endpoint = json.loads(result.stdout)[0]['Endpoints']['docker']['Host']
    if not endpoint.startswith('unix:///') or not Path(endpoint[len('unix://'):]).is_socket():
        raise ValueError('local-docker-socket-required')
    for key in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        process_env.pop(key, None)
    process_env['DOCKER_HOST'] = endpoint
    tokens = shlex.split((install_dir / '.compose-flags').read_text())
    if len(tokens) % 2 or any(value != '-f' for value in tokens[::2]):
        raise ValueError('resolved-legacy-compose-flags-required')
    fragments = helper('install').FRAGMENTS
    removed = {install_dir / value for value in (*fragments, 'extensions/services/pixel-vm-link/compose.yaml')}
    files = []
    for value in tokens[1::2]:
        path = (install_dir / value).resolve(strict=True)
        if install_dir not in path.parents:
            raise ValueError('installed-compose-file-required')
        if path not in removed and path not in files: files.append(path)
    files.extend(install_dir / value for value in fragments)
    files.append(preparation / 'storage.compose.json')
    base = [docker, 'compose', '--project-directory', str(install_dir), '--project-name', project]
    command = [*base, '--env-file', str(install_dir / '.env')]
    for path in files: command.extend(['-f', str(path)])
    previous = [*base, '--env-file', '/dev/null', '-f', str(preparation / 'rollback.compose.json')]
    def execute(command, args, timeout, input):
        return subprocess.run([*command, *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, timeout=timeout, input=input)
    def run(*args, timeout=60, input=None): return execute(command, args, timeout, input)
    def previous_run(*args, timeout=60, input=None): return execute(previous, args, timeout, input)
    def docker_run(*args, timeout=60, input=None): return execute([docker], args, timeout, input)
    if run('config', '--quiet').returncode or previous_run('config', '--quiet').returncode:
        raise ValueError('prepared-compose-configuration-invalid')
    if not apply:
        return {'status': 'planned', 'servicesChanged': False}
    latest = {}
    def checkpoint(record):
        nonlocal latest
        temporary = preparation / '.docker-migration.json.tmp'
        with temporary.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write((json.dumps(record, sort_keys=True) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, journal)
        directory = os.open(preparation, os.O_RDONLY | os.O_DIRECTORY)
        try: os.fsync(directory)
        finally: os.close(directory)
        latest = record
    try:
        record = compose.migrate_infrastructure(run, docker_run,
            ingress=receipt['legacyStorage']['containers']['ingress'], image=transport['image'],
            user=transport['user'], project=project, transaction=receipt['runtimeDigest'],
            storage=documents['storage.compose.json'], workspace=values['PIXEL_NATIVE_WORKSPACE'],
            dashboard_key=values['DASHBOARD_API_KEY'], checkpoint=checkpoint)
        compose.finish_migration_infrastructure(run, docker_run, record,
            dashboard_key=values['DASHBOARD_API_KEY'], checkpoint=checkpoint)
    except BaseException:
        if latest and latest.get('phase') != 'releasing-admission':
            compose.restore_migration_infrastructure(previous_run, docker_run, latest,
                dashboard_key=values['DASHBOARD_API_KEY'], checkpoint=checkpoint)
        raise
    return {'status': 'docker-ready', 'requiresNativeActivation': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.preparation, apply=args.apply)))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print('Docker migration stopped. Preserve its private journal and recovery snapshots; do not repeat automatically.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
