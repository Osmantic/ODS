"""Run the closed macOS CLI lifecycle body under its parent's admission lease.

The child inherits the already-held selection descriptor. It cannot enter the
internal CLI dispatch from an ambient bypass flag: its private journal, parent
identity, exact action, source bytes, and inherited descriptor are verified.
"""
import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ENV_KEYS = ('ODS_NATIVE_LIFECYCLE_JOURNAL', 'ODS_NATIVE_LIFECYCLE_TOKEN', 'ODS_NATIVE_LIFECYCLE_FD',
    'ODS_NATIVE_LIFECYCLE_DOCKER')


def helper(name):
    spec = importlib.util.spec_from_file_location('lifecycle_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def environment():
    return helper('pixel-native-env')


def validate_action(action, service):
    if (action not in ('start', 'restart', 'update') or service not in ('', 'pixel-native-ingress')
            or action == 'update' and service):
        raise ValueError('native-lifecycle-action-required')


def process_identity(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,lstart=,comm='],
        capture_output=True, text=True, check=True, timeout=5)
    value = result.stdout.strip()
    if not value or value.split()[0] != str(pid):
        raise ValueError('native-lifecycle-parent-required')
    return value


def process_birth(pid):
    result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'pid=,lstart='],
        capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else None


def require_previous_gone(record):
    for field in ('child', 'preparationCommand'):
        previous = record.get(field)
        if previous and previous.get('pid') and process_birth(previous['pid']) == previous['birth']:
            raise ValueError('native-lifecycle-previous-child-still-live')


def execute(command, *, env, descriptor, record, field, checkpoint, directory, timeout):
    """Journal the gated child before it can invoke Docker or the actual CLI.

    EOF before the gate opens means no command ran. A timed-out observer leaves
    the child and inherited lease intact; a new attempt must prove it is gone.
    """
    require_previous_gone(record)
    reader, writer = os.pipe()
    log = directory / (field + '-' + secrets.token_hex(8) + '.log')
    gate = ('import os,sys; fd=int(sys.argv[1]); approved=os.read(fd,1); os.close(fd); '
        'approved==b"G" or sys.exit(125); os.execvpe(sys.argv[2],sys.argv[2:],os.environ)')
    try:
        with log.open('xb') as stream:
            os.fchmod(stream.fileno(), 0o600)
            child = subprocess.Popen([sys.executable, '-c', gate, str(reader), *command],
                cwd=directory, env=env, stdout=stream, stderr=subprocess.STDOUT,
                pass_fds=(descriptor, reader))
            os.close(reader)
            reader = None
            record[field] = {'pid': child.pid, 'birth': process_birth(child.pid),
                'log': str(log), 'outcome': 'running', 'command': command}
            if not record[field]['birth']:
                raise ValueError('native-lifecycle-child-unconfirmed')
            checkpoint(record)
            os.write(writer, b'G')
            os.close(writer)
            writer = None
            try:
                code = child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                record[field]['outcome'] = 'unconfirmed'
                checkpoint(record)
                raise ValueError('native-lifecycle-child-unconfirmed') from None
        record[field].update(exitCode=code, outcome='completed' if code == 0 else 'failed')
        checkpoint(record)
        return code
    finally:
        if reader is not None: os.close(reader)
        if writer is not None: os.close(writer)


def source_binding(install_dir, bash, action, service):
    validate_action(action, service)
    cli = install_dir / 'installers/macos/ods-macos.sh'
    paths = (cli, *sorted(HERE.glob('*.py')), *sorted(HERE.glob('*.sh')),
        *sorted((install_dir / 'lib').glob('*.sh')), *sorted((install_dir / 'scripts').glob('*.sh')),
        *sorted((install_dir / 'scripts').glob('*.py')))
    if (not bash.is_absolute() or not bash.is_file() or not os.access(bash, os.X_OK)
            or any(path.resolve(strict=True) != path or not path.is_file() for path in paths)):
        raise ValueError('native-lifecycle-source-required')
    document = {'action': action, 'service': service, 'bash': str(bash),
        'bashSHA256': hashlib.sha256(bash.read_bytes()).hexdigest(),
        'files': {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}}
    return document, hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()


def cli_operation(*, install_dir, action, service, bash, descriptor, process_env,
                  read_selection, run, docker_run, retry_image_preparation=False, docker_binary=None):
    install_dir = Path(install_dir).resolve(strict=True)
    bash = Path(bash or '').resolve(strict=True)
    binding, key = source_binding(install_dir, bash, action, service)
    preparation = install_dir / 'data/pixel-native/preparation'
    def validate_result(record):
        if source_binding(install_dir, bash, action, service)[1] != key:
            raise ValueError('native-lifecycle-source-changed')
        stack = helper('pixel-native-lifecycle-stack')
        files, document = read_selection()
        saved = record['lifecycleStack']
        if stack.capture(install_dir, files=files, document=document, bind_build_inputs=action != 'restart') != saved['inputs']:
            raise ValueError('native-lifecycle-stack-inputs-changed')
        stack.verify_result(saved, docker_run, install_dir)
    def operation(selection, record, checkpoint):
        if source_binding(install_dir, bash, action, service)[1] != key:
            raise ValueError('native-lifecycle-source-changed')
        name = json.loads(environment().snapshot(preparation / 'ingress-refresh-current.json')[0])['directory']
        if not re.fullmatch('ingress-refresh-[a-f0-9]{32}', name):
            raise ValueError('native-lifecycle-journal-required')
        journal = preparation / name / 'refresh.json'
        require_previous_gone(record)
        stack = helper('pixel-native-lifecycle-stack')
        def prepare(*args, **kwargs):
            return run.prepare(*args, record=record, checkpoint=checkpoint, directory=journal.parent,
                descriptor=descriptor, **kwargs)
        stack.freeze(install_dir, journal.parent, record, action=action, service=service,
            read_selection=read_selection, run=prepare if hasattr(run, 'prepare') else run,
            docker_run=docker_run, checkpoint=checkpoint, retry_preparation=retry_image_preparation)
        token = secrets.token_hex(32)
        record['childAuthority'] = {'parent': os.getpid(), 'parentIdentity': process_identity(os.getpid()),
            'token': token, 'descriptor': descriptor, 'source': binding, 'sourceKey': key,
            'docker': docker_binary or ''}
        checkpoint(record)
        child_env = {k: v for k, v in process_env.items() if k not in (*ENV_KEYS, 'BASH_ENV', 'ENV')}
        child_env.update(ODS_INSTALL_DIR=str(install_dir), ODS_HOME=str(install_dir),
            ODS_PYTHON_CMD=sys.executable, ODS_NATIVE_LIFECYCLE_JOURNAL=str(journal),
            ODS_NATIVE_LIFECYCLE_TOKEN=token, ODS_NATIVE_LIFECYCLE_FD=str(descriptor),
            ODS_NATIVE_LIFECYCLE_DOCKER=docker_binary or '')
        code = execute([str(bash), str(install_dir / 'installers/macos/ods-macos.sh'),
            '__native-lifecycle', action, service], env=child_env, descriptor=descriptor, record=record,
            field='child', checkpoint=checkpoint, directory=journal.parent, timeout=900)
        if code:
            raise ValueError('native-lifecycle-child-failed')
        if source_binding(install_dir, bash, action, service)[1] != key:
            raise ValueError('native-lifecycle-source-changed')
        # Re-resolve discovery and interpolation as well as file identities;
        # newly added extensions must not slip into a successful release.
        stack.freeze(install_dir, journal.parent, record, action=action, service=service,
            read_selection=read_selection, run=run, docker_run=docker_run, checkpoint=checkpoint)
        validate_result(record)
        return {'action': action, 'service': service, 'exitCode': code}
    operation.validate_result = validate_result
    return operation, 'cli-' + action, key


def verify_child(install_dir, action, service):
    validate_action(action, service)
    install_dir = Path(install_dir).resolve(strict=True)
    preparation = install_dir / 'data/pixel-native/preparation'
    journal = Path(os.environ.get(ENV_KEYS[0], ''))
    if (journal.name != 'refresh.json' or journal.parent.parent != preparation
            or not re.fullmatch('ingress-refresh-[a-f0-9]{32}', journal.parent.name)
            or journal.resolve(strict=True) != journal):
        raise ValueError('native-lifecycle-journal-required')
    record = json.loads(environment().snapshot(journal)[0])
    authority = record['childAuthority']
    token = os.environ.get(ENV_KEYS[1], '')
    if (record.get('phase') != 'operating' or not re.fullmatch('[a-f0-9]{64}', token)
            or not secrets.compare_digest(token, authority['token'])):
        raise ValueError('native-lifecycle-authority-required')
    source = authority['source']
    if os.environ.get('ODS_NATIVE_LIFECYCLE_DOCKER', '') != authority['docker']:
        raise ValueError('native-lifecycle-docker-selection-changed')
    if (source['action'] != action or source['service'] != service
            or source_binding(install_dir, Path(source['bash']), action, service)[1] != authority['sourceKey']
            or record['operation'] != {'kind': 'cli-' + action, 'key': authority['sourceKey']}):
        raise ValueError('native-lifecycle-selection-changed')
    descriptor = int(os.environ.get(ENV_KEYS[2], '-1'))
    lock = preparation / '.selection.lock'
    expected = lock.lstat()
    inherited = os.fstat(descriptor)
    if (descriptor != authority['descriptor'] or not stat.S_ISREG(expected.st_mode)
            or expected.st_uid != os.getuid() or stat.S_IMODE(expected.st_mode) != 0o600
            or expected.st_nlink != 1 or (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino)):
        raise ValueError('native-lifecycle-inherited-lease-required')
    another = os.open(lock, os.O_RDWR | os.O_NOFOLLOW)
    try:
        try:
            fcntl.flock(another, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            raise ValueError('native-lifecycle-live-lease-required')
    finally:
        os.close(another)
    # Command substitution can add a Bash subshell. Walk only a bounded chain
    # to the exact recorded child, then require its original live Python owner.
    parent = os.getppid()
    for _ in range(6):
        if parent == record['child']['pid']: break
        observed = subprocess.run(['/bin/ps', '-p', str(parent), '-o', 'ppid='],
            capture_output=True, text=True, check=True, timeout=5).stdout.strip()
        parent = int(observed)
    else:
        raise ValueError('native-lifecycle-child-ancestry-required')
    actual_parent = subprocess.run(['/bin/ps', '-p', str(parent), '-o', 'ppid='],
        capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    if (process_birth(parent) != record['child']['birth'] or actual_parent != str(authority['parent'])
            or process_identity(authority['parent']) != authority['parentIdentity']):
        raise ValueError('native-lifecycle-parent-changed')
    saved = record['lifecycleStack']
    if (saved['phase'] != 'selected' or saved['action'] != action or saved['service'] != service
            or Path(saved['path']) != journal.parent / 'lifecycle.compose.json'):
        raise ValueError('native-lifecycle-frozen-stack-required')
    helper('pixel-native-lifecycle-stack').validate_saved(saved)
    return saved


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--verify-child', action='store_true', required=True)
    parser.add_argument('--action', choices=('start', 'restart', 'update'), required=True)
    parser.add_argument('--service', choices=('', 'pixel-native-ingress'), default='')
    parser.add_argument('--frozen-flags', action='store_true')
    args = parser.parse_args()
    try:
        saved = verify_child(args.install_dir, args.action, args.service)
        if args.frozen_flags:
            relative = str(Path(saved['path']).relative_to(Path(args.install_dir)))
            if any(char.isspace() or char in '*?[]' for char in relative):
                raise ValueError('native-lifecycle-flags-required')
            print('-f ' + relative)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError):
        print('Native lifecycle child has no verified live transaction.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
