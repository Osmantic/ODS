"""Native owner-run extension manager; no broker-group credential sharing.

Publication and runtime ACL provisioning are separate privileged installer
steps. This renderer never starts a service or reads the ODS credential.
"""
import importlib.util
import hashlib
import os
from pathlib import Path
import plistlib
import pwd
import re
import stat
import sys


SPEC = importlib.util.spec_from_file_location('manager_policy',
    Path(__file__).resolve().parents[3] / 'bin/pixel_macos_policy.py')
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def operations_helpers():
    spec = importlib.util.spec_from_file_location('manager_publication_helpers',
        Path(__file__).with_name('pixel-native-ops-service.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(*, python, program_root, environment, owner, port,
           runtime='/private/var/lib/ods-pixel-manager',
           results='/private/var/lib/pixel-ops-broker/results',
           logs='/private/var/log/ods-pixel-manager', label='com.ods.pixel-native-manager'):
    if (not isinstance(owner, str) or owner == 'root'
            or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', owner)
            or type(port) is not int or not 1 <= port <= 65535
            or not re.fullmatch(r'com\.ods\.pixel-native-manager(?:\.[a-z0-9-]+)?', label)):
        raise ValueError('invalid-native-manager-identity')
    python, program_root, environment, runtime, results, logs = [Path(policy._path(str(value)))
        for value in (python, program_root, environment, runtime, results, logs)]
    for mutable in (runtime, logs):
        for fixed in (python, program_root, environment, results):
            if mutable == fixed or mutable in fixed.parents or fixed in mutable.parents:
                raise ValueError('native-manager-boundary-overlap')
    quoted = lambda path: policy._quoted(str(path))
    paths = lambda values: ' '.join('(subpath ' + quoted(value) + ')' for value in values)
    literals = lambda values: ' '.join('(literal ' + quoted(value) + ')' for value in values)
    endpoint = runtime / 'extension-manager.sock'
    profile = '\n'.join([
        '(version 1)', '(deny default)', '(import "system.sb")',
        '(allow process-exec ' + literals([python]) + ')', '(deny process-fork)',
        '(allow sysctl-read file-read-metadata)',
        '(allow file-read* ' + paths(['/System', '/usr', '/Library/Developer', program_root, results]) + ')',
        '(allow file-read* ' + literals([environment, '/private/etc/passwd', '/private/etc/group']) + ')',
        '(allow file-read* file-write* ' + paths([runtime, logs]) + ')',
        '(allow file-read* file-write* ' + literals(['/dev/null', '/dev/urandom', '/dev/random']) + ')',
        '(allow network-bind network-inbound network-outbound ' + literals([endpoint]) + ')',
        '(allow network-outbound (remote tcp "localhost:' + str(port) + '"))',
        '(deny file-write* ' + paths([program_root, results]) + ' ' + literals([environment, python]) + ')',
        '(deny file-write-setugid file-write-mount file-write-umount)', '',
    ]).encode()
    document = {'Label': label, 'UserName': owner,
        'ProgramArguments': ['/usr/bin/env', '-i', 'PATH=/usr/bin:/bin', 'HOME=' + str(environment.parent),
            'TMPDIR=' + str(runtime), '/usr/bin/sandbox-exec', '-f', str(program_root / 'manager.sb'),
            str(python), '-I', '-B', str(program_root / 'extension_manager.py'),
            'serve', str(endpoint), str(environment), str(port)],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10, 'ExitTimeOut': 20,
        'Umask': 0o077, 'WorkingDirectory': '/', 'ProcessType': 'Background',
        'StandardInPath': '/dev/null', 'StandardOutPath': str(logs / 'stdout.log'),
        'StandardErrorPath': str(logs / 'stderr.log')}
    return {'profile': profile, 'plist': plistlib.dumps(document, sort_keys=True)}


def publish(*, sources, expected_sha256, python, program_root, environment, owner, port,
            definition, runtime='/private/var/lib/ods-pixel-manager',
            results='/private/var/lib/pixel-ops-broker/results',
            logs='/private/var/log/ods-pixel-manager', label='com.ods.pixel-native-manager'):
    """Publish approved source under the caller's lock; do not start services.

    Source hashes must be bound to the selected ODS revision by orchestration.
    Credentials stay in the owner file and are never copied into this bundle.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    names = {'extension_manager.py', 'unix_peer.py'}
    if (type(sources) is not dict or type(expected_sha256) is not dict
            or set(sources) != names or set(expected_sha256) != names):
        raise ValueError('approved-manager-source-set-required')
    for name in sorted(names):
        body, digest = sources[name], expected_sha256[name]
        if (type(body) is not bytes or not 0 < len(body) <= 1024 * 1024
                or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
                or hashlib.sha256(body).hexdigest() != digest):
            raise ValueError('approved-manager-snapshot-required')
        compile(body, name, 'exec')
    rendered = render(python=python, program_root=program_root, environment=environment,
        owner=owner, port=port, runtime=runtime, results=results, logs=logs, label=label)
    entry = pwd.getpwnam(owner)
    info = Path(environment).lstat()
    if (entry.pw_uid == 0 or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != entry.pw_uid or info.st_mode & 0o077
            or not info.st_mode & 0o400 or info.st_size > 2 * 1024 * 1024):
        raise ValueError('unsafe-native-manager-environment')
    program_root, definition = Path(program_root), Path(definition)
    if definition.name != label + '.plist':
        raise ValueError('manager-definition-label-mismatch')
    helpers = operations_helpers()
    helpers.custody.protected_bytes(str(python), limit=128 * 1024 * 1024)
    installer = helpers.installer_helpers()
    files = [(program_root / name, sources[name]) for name in sorted(names)]
    files.extend([(program_root / 'manager.sb', rendered['profile']), (definition, rendered['plist'])])
    for path, body in files:
        installer._preflight_file(path, body, mode=0o644, uid=0, gid=0)
    prepare_logs(logs, uid=entry.pw_uid, gid=entry.pw_gid)
    for path, body in files:
        installer._write_exact(path, body, mode=0o644, uid=0, gid=0)
    return definition


def prepare_logs(directory, *, uid, gid):
    """Launchd opens owner job logs before exec; preserve existing contents."""
    if type(uid) is not int or uid <= 0 or type(gid) is not int or gid < 0:
        raise ValueError('native-manager-log-owner-required')
    with operations_helpers().custody.protected_directory(directory, create=True) as parent:
        for name in ('stdout.log', 'stderr.log'):
            created = False
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                created = True
            except FileExistsError:
                fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
            try:
                if created: os.fchown(fd, uid, gid)
                info = os.fstat(fd)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != uid or info.st_gid != gid or stat.S_IMODE(info.st_mode) != 0o600):
                    raise ValueError('unsafe-native-manager-log')
            finally:
                os.close(fd)
