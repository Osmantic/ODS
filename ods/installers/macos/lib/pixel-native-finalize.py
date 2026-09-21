"""Publish owner-side Compose selection after a verified native migration.

This does not activate services or grant access. Protected completion evidence
is checked read-only through sudo before publishing disposable owner receipts.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def helper(name):
    spec = importlib.util.spec_from_file_location('native_finalize_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def protected_proof(owner, runtime, services, source_ref):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('protected-macos-verification-required')
    if any(not re.fullmatch('[a-f0-9]{' + str(size) + '}', value)
           for value, size in ((runtime, 64), (services, 64), (source_ref, 40))):
        raise ValueError('invalid-native-selection')
    installer = helper('pixel-macos-access-install')
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    from pixel_access_bridge import private_json
    from pixel_macos_custody import protected_bytes
    state = Path('/private/var/lib/ods-pixel-access')
    pending = state / 'runtime-upgrade.json'
    if os.path.lexists(pending):
        raise ValueError('native-upgrade-still-pending')
    completed = state / ('runtime-upgrade-' + runtime + '.completed.json')
    record = private_json(completed, 0, 32 * 1024 * 1024)
    if record.get('phase') != 'active' or record.get('candidateDigest') != runtime:
        raise ValueError('native-upgrade-not-active')
    # The root-owned journal is the authority for the activated file set.
    for item in record['files']:
        body = base64.b64decode(item['after'], validate=True)
        if (hashlib.sha256(body).hexdigest() != item['afterSha256']
                or protected_bytes(item['path'], limit=8 * 1024 * 1024) != body):
            raise ValueError('native-activated-files-changed')
    service_record = private_json(state / 'service-installation.json', 0, 2 * 1024 * 1024)
    selection = service_record['selection']
    if selection.get('expected_digest') != services or selection.get('expected_ref') != source_ref:
        raise ValueError('native-services-selection-mismatch')
    installer._verify_new_services({'owner': pwd.getpwnam(owner), 'native_services': selection})
    for label in ('pixel-native-gateway', 'pixel-access', 'pixel-access-relay'):
        result = subprocess.run(['/bin/launchctl', 'print', 'system/com.ods.' + label],
            capture_output=True, text=True, timeout=15, check=True)
        if not re.search(r'^\s*state = running\s*$', result.stdout, re.M):
            raise ValueError('native-job-not-running')
    if os.path.lexists(pending) or private_json(completed, 0, 32 * 1024 * 1024) != record:
        raise ValueError('native-upgrade-changed-during-verification')
    return {'status': 'active', 'runtimeDigest': runtime, 'serviceDigest': services}


def selection_records(prepared, docker_prepared, docker_record, storage, proof):
    if (prepared.get('kind') != 'legacy-native' or prepared.get('status') != 'prepared'
            or prepared.get('phase') != 'awaiting-joint-activation'
            or docker_prepared.get('kind') != 'legacy-native'
            or docker_prepared.get('environmentStatus') != 'configured'
            or docker_record.get('phase') != 'infrastructure-ready'
            or docker_record.get('requiresRecovery') is not False
            or proof.get('status') != 'active'):
        raise ValueError('completed-native-migration-required')
    for field in ('runtimeDigest', 'currentDigest', 'installDir'):
        if not prepared.get(field) or prepared[field] != docker_prepared.get(field):
            raise ValueError('native-docker-migration-mismatch')
    for field in ('runtimeDigest', 'serviceDigest'):
        if proof.get(field) != prepared.get(field):
            raise ValueError('native-activation-proof-mismatch')
    digest = hashlib.sha256(json.dumps(storage, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    if digest != docker_prepared.get('storageDigest'):
        raise ValueError('native-migration-storage-mismatch')
    receipt = {**prepared, 'storageDigest': digest}
    activation = {'schemaVersion': 1, 'status': 'ready', 'phase': 'services-ready',
        'runtimeDigest': prepared['runtimeDigest'], 'serviceDigest': prepared['serviceDigest'],
        'storageDigest': digest}
    return receipt, activation


def update_selection_records(previous, activation, prepared, proof, install_dir):
    replay = all(prepared.get(key) == previous.get(key) for key in ('runtimeDigest', 'serviceDigest'))
    if (prepared.get('kind') != 'legacy-native' or prepared.get('status') != 'prepared'
            or prepared.get('phase') != 'awaiting-joint-activation'
            or prepared.get('installDir') != str(install_dir)
            or not re.fullmatch('[a-f0-9]{40}', str(prepared.get('pixelSourceRef', '')))
            or (not replay and prepared.get('currentDigest') != previous.get('runtimeDigest'))
            or activation.get('runtimeDigest') != previous.get('runtimeDigest')
            or activation.get('serviceDigest') != previous.get('serviceDigest')
            or activation.get('status') != 'ready' or activation.get('phase') != 'services-ready'
            or proof.get('status') != 'active'):
        raise ValueError('native-update-selection-mismatch')
    for field in ('runtimeDigest', 'serviceDigest'):
        if (not re.fullmatch('[a-f0-9]{64}', str(prepared.get(field, '')))
                or proof.get(field) != prepared[field]):
            raise ValueError('native-update-proof-mismatch')
    # Keep the original home and external-volume selection; only runtime identity changes.
    identities = {key: prepared[key] for key in ('runtimeDigest', 'serviceDigest')}
    return {'schemaVersion': 1, 'preparation': {**previous, **identities,
        'pixelSourceRef': prepared['pixelSourceRef']},
        'activation': {**activation, **identities}}


def refresh_clients(install_dir):
    """Recreate native consumers from the resolved stack, preserving volumes.

    The owner selection must already be published and protected activation
    verified. Failure leaves that selection intact for an explicit replay.
    """
    install_dir = Path(install_dir).resolve(strict=True)
    installer, stack = helper('pixel-macos-access-install'), helper('pixel-native-stack')
    owner = pwd.getpwuid(os.getuid())
    _, environment, _, _, _, _ = installer._source_gateway(
        installer._launchd.GATEWAY_PLIST, owner.pw_name, 18789)
    transport = {key: environment[name] for key, name in (
        ('docker', 'PIXEL_HISTORY_DOCKER'), ('project', 'PIXEL_HISTORY_PROJECT'),
        ('image', 'PIXEL_HISTORY_IMAGE'), ('user', 'PIXEL_HISTORY_USER'))}
    installer._native_transport_environment(transport, owner)
    endpoint = environment.get('DOCKER_HOST', '')
    if not endpoint.startswith('unix:///') or not Path(endpoint[7:]).is_socket():
        raise ValueError('installed-local-docker-socket-required')
    process_env = {key: value for key, value in os.environ.items()
        if key not in ('DOCKER_CONTEXT', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH', 'DOCKER_HOST')}
    process_env['DOCKER_HOST'] = endpoint
    tokens = shlex.split((install_dir / '.compose-flags').read_text())
    if not tokens or len(tokens) % 2 or any(token != '-f' for token in tokens[::2]):
        raise ValueError('resolved-compose-flags-required')
    command = [transport['docker'], 'compose', '--project-directory', str(install_dir),
        '--project-name', transport['project'], '--env-file', str(install_dir / '.env')]
    for value in stack.resolve_files(install_dir, tokens[1::2]):
        path = (install_dir / value).resolve(strict=True)
        if install_dir not in path.parents or not path.is_file():
            raise ValueError('installed-compose-file-required')
        command.extend(['-f', str(path)])
    def run(*args, timeout=30):
        return subprocess.run([*command, *args], cwd=install_dir, env=process_env,
            capture_output=True, text=True, check=True, timeout=timeout)
    resolved = json.loads(run('config', '--format', 'json').stdout)
    if resolved.get('name') != transport['project']:
        raise ValueError('native-compose-project-mismatch')
    for name in ('dashboard-api', 'open-webui'):
        definition = resolved.get('services', {}).get(name)
        if type(definition) is not dict:
            raise ValueError('native-client-service-missing')
        hosts = definition.get('extra_hosts', {})
        if type(hosts) not in (dict, list):
            raise ValueError('native-client-hosts-invalid')
        if any(str(host).split('=', 1)[0].split(':', 1)[0].lower().rstrip('.') == 'pixel-edge' for host in hosts):
            raise ValueError('native-client-has-legacy-edge-route')
    run('up', '-d', '--no-deps', '--wait', '--wait-timeout', '120',
        'dashboard-api', 'open-webui', timeout=180)
    probe = ('import urllib.request; '
        'response=urllib.request.build_opener(urllib.request.ProxyHandler({})).open('
        '"http://pixel-edge:9595/health",timeout=15); '
        'raise SystemExit(0 if response.status==200 else 1)')
    run('exec', '-T', 'dashboard-api', 'python3', '-c', probe)


def finalize_update(preparation):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    config, stack = helper('pixel-native-config'), helper('pixel-native-stack')
    prepared = config.private_json(Path(preparation) / 'preparation.json')
    install_dir = Path(prepared['installDir']).resolve(strict=True)
    directory = install_dir / 'data/pixel-native/preparation'
    if directory.resolve(strict=True) != directory:
        raise ValueError('canonical-native-selection-required')
    lock = os.open(directory / '.selection.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(lock)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise ValueError('private-native-selection-lock-required')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stack.resolve_files(install_dir, [])
        previous, activation = stack.read_selection(directory)
        result = subprocess.run(['/usr/bin/sudo', '/usr/bin/python3', str(Path(__file__).resolve()),
            '--verify-protected', '--owner', pwd.getpwuid(os.getuid()).pw_name,
            '--runtime', prepared['runtimeDigest'], '--services', prepared['serviceDigest'],
            '--source-ref', prepared['pixelSourceRef']], stdout=subprocess.PIPE,
            text=True, timeout=180, check=True)
        document = update_selection_records(previous, activation, prepared, json.loads(result.stdout), install_dir)
        # One atomic record avoids a mismatched preparation/activation pair after a crash.
        with tempfile.TemporaryDirectory(prefix='.selection-', dir=directory) as temporary:
            staged = Path(temporary) / 'selection.json'
            with staged.open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write((json.dumps(document, sort_keys=True) + '\n').encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(staged, directory / stack.UPDATE_SELECTION)
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        refresh_clients(install_dir)
    finally:
        os.close(lock)
    return {'status': 'selection-ready', 'path': str(directory / stack.UPDATE_SELECTION)}


def finalize(preparation, docker_preparation):
    if sys.platform != 'darwin' or os.geteuid() == 0:
        raise ValueError('native-macos-owner-required')
    config = helper('pixel-native-config')
    preparation, docker_preparation = Path(preparation), Path(docker_preparation)
    prepared = config.private_json(preparation / 'preparation.json')
    docker_prepared = config.private_json(docker_preparation / 'preparation.json')
    docker_record = config.private_json(docker_preparation / 'docker-migration.json')
    storage = config.private_json(docker_preparation / 'storage.compose.json')
    install_dir = Path(prepared['installDir']).resolve(strict=True)
    destination = install_dir / 'data/pixel-native/preparation'
    result = subprocess.run(['/usr/bin/sudo', '/usr/bin/python3', str(Path(__file__).resolve()),
        '--verify-protected', '--owner', pwd.getpwuid(os.getuid()).pw_name,
        '--runtime', prepared['runtimeDigest'], '--services', prepared['serviceDigest'],
        '--source-ref', prepared['pixelSourceRef']], capture_output=False, stdout=subprocess.PIPE,
        text=True, timeout=180, check=True)
    receipt, activation = selection_records(prepared, docker_prepared, docker_record, storage,
        json.loads(result.stdout))
    if os.path.lexists(destination):
        expected = {'preparation.json': receipt, 'activation.json': activation, 'storage.compose.json': storage}
        if any(config.private_json(destination / name) != value for name, value in expected.items()):
            raise ValueError('installed-native-selection-already-exists')
        refresh_clients(install_dir)
        return {'status': 'selection-ready', 'path': str(destination)}
    transport = docker_prepared['nativeTransport']
    for name in helper('pixel-native-compose').SERVICES:
        result = subprocess.run([transport['docker'], 'ps', '--filter',
            'label=com.docker.compose.project=' + transport['project'], '--filter',
            'label=com.docker.compose.service=' + name, '--format', '{{.ID}}'],
            capture_output=True, text=True, timeout=15, check=True)
        ids = result.stdout.split()
        if len(ids) != 1:
            raise ValueError('native-compose-service-missing')
        result = subprocess.run([transport['docker'], 'inspect', '--format', '{{json .State}}', ids[0]],
            capture_output=True, text=True, timeout=15, check=True)
        state = json.loads(result.stdout)
        if state.get('Running') is not True or state.get('Health', {}).get('Status') != 'healthy':
            raise ValueError('native-compose-service-not-healthy')
    # Publish all three records together. No credentials or rollback payloads
    # are copied from the preparation into the management selection.
    with tempfile.TemporaryDirectory(prefix='.native-selection-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'preparation'
        staged.mkdir(mode=0o700)
        for filename, document in (('preparation.json', receipt), ('activation.json', activation),
                ('storage.compose.json', storage)):
            with (staged / filename).open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write((json.dumps(document, sort_keys=True) + '\n').encode())
                stream.flush()
                os.fsync(stream.fileno())
        fd = os.open(staged, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        if os.path.lexists(destination):
            raise ValueError('installed-native-selection-appeared')
        os.rename(staged, destination)
        fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    refresh_clients(install_dir)
    return {'status': 'selection-ready', 'path': str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--preparation')
    parser.add_argument('--docker-preparation')
    parser.add_argument('--verify-protected', action='store_true')
    parser.add_argument('--update-existing', action='store_true')
    for name in ('owner', 'runtime', 'services', 'source-ref'):
        parser.add_argument('--' + name)
    args = parser.parse_args()
    try:
        if args.verify_protected:
            result = protected_proof(args.owner, args.runtime, args.services, args.source_ref)
        elif args.update_existing:
            result = finalize_update(args.preparation)
        else:
            result = finalize(args.preparation, args.docker_preparation)
        print(json.dumps(result))
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('Native selection finalization failed (' + type(error).__name__ + '); preserve receipts.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
