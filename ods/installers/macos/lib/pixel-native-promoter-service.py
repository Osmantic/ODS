"""Render and publish the root download promoter's native filesystem boundary.

Publication requires approved source snapshots and root custody checks.
Rendering is side-effect free; neither entry point starts a daemon.
"""
import importlib.util
import hashlib
import os
from pathlib import Path
import plistlib
import pwd
import re
import sys


SPEC = importlib.util.spec_from_file_location('promoter_policy',
    Path(__file__).resolve().parents[3] / 'bin/pixel_macos_policy.py')
policy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(policy)


def operations_helpers():
    spec = importlib.util.spec_from_file_location('promoter_publication_helpers',
        Path(__file__).with_name('pixel-native-ops-service.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(*, python, program_root, workspace, owner,
           state='/private/var/lib/pixel-ops-broker',
           runtime='/private/var/lib/ods-pixel-artifact-promoter',
           logs='/private/var/log/ods-pixel-promoter',
           label='com.ods.pixel-native-promoter'):
    if (not isinstance(owner, str) or not re.fullmatch(r'[a-z_][a-z0-9_-]{0,31}', owner)
            or owner == 'root' or not re.fullmatch(r'com\.ods\.pixel-native-promoter(?:\.[a-z0-9-]+)?', label)):
        raise ValueError('invalid-native-promoter-identity')
    python, program_root, workspace, state, runtime, logs = [Path(policy._path(str(value)))
        for value in (python, program_root, workspace, state, runtime, logs)]
    roots = (program_root, workspace, state, runtime, logs)
    if any(a == b or a in b.parents or b in a.parents
           for index, a in enumerate(roots) for b in roots[index + 1:]):
        raise ValueError('native-promoter-roots-overlap')
    if any(root == python or root in python.parents for root in (workspace, runtime, logs)):
        raise ValueError('native-promoter-writable-interpreter')
    socket = runtime / 'promoter.sock'
    quoted = lambda path: policy._quoted(str(path))
    paths = lambda values: ' '.join('(subpath ' + quoted(value) + ')' for value in values)
    literals = lambda values: ' '.join('(literal ' + quoted(value) + ')' for value in values)
    # Use the path-filtered Unix operations provided by Darwin's Seatbelt;
    # no ip/remote rule is present, and no subprocess is needed for promotion.
    profile = '\n'.join([
        '(version 1)', '(deny default)', '(import "system.sb")',
        '(allow process-exec ' + literals([python]) + ')',
        '(deny process-fork)', '(allow sysctl-read)', '(allow file-read-metadata)',
        '(allow file-read* ' + paths(['/System', '/usr', '/Library/Developer', program_root,
                                     state / 'results', state / 'artifacts', workspace]) + ')',
        '(allow file-read* ' + literals(['/private/etc/passwd', '/private/etc/group', '/private/etc/localtime']) + ')',
        '(allow file-read* ' + literals(list(runtime.parents)) + ')',
        '(allow file-read* file-write* ' + paths([workspace, runtime, logs]) + ')',
        '(allow file-read* file-write* ' + literals(['/dev/null', '/dev/urandom', '/dev/random']) + ')',
        '(allow network-bind network-inbound network-outbound ' + literals([socket]) + ')',
        '(deny file-write* ' + paths([program_root, state]) + ')',
        '(deny file-write-unlink ' + literals(sorted({p for root in roots for p in root.parents if p != Path('/')})) + ')',
        '(deny file-write-setugid file-write-mount file-write-umount)', '',
    ]).encode()
    environment = {'HOME': str(runtime), 'PATH': '/usr/bin:/bin', 'TMPDIR': str(runtime)}
    document = {'Label': label, 'UserName': 'root', 'GroupName': 'wheel',
        'ProgramArguments': ['/usr/bin/env', '-i', *(key + '=' + value for key, value in environment.items()),
            '/usr/bin/sandbox-exec', '-f', str(program_root / 'promoter.sb'),
            str(python), '-I', '-B', str(program_root / 'artifact_promoter.py'),
            'serve', str(socket), str(state / 'results'), str(state / 'artifacts'), str(workspace), owner],
        'RunAtLoad': True, 'KeepAlive': True, 'ThrottleInterval': 10, 'ExitTimeOut': 20,
        'Umask': 0o077, 'WorkingDirectory': '/', 'ProcessType': 'Background',
        'StandardInPath': '/dev/null', 'StandardOutPath': str(logs / 'stdout.log'),
        'StandardErrorPath': str(logs / 'stderr.log')}
    return {'profile': profile, 'plist': plistlib.dumps(document, sort_keys=True)}


def publish(*, sources, expected_sha256, python, program_root, workspace, owner,
            definition, state='/private/var/lib/pixel-ops-broker',
            runtime='/private/var/lib/ods-pixel-artifact-promoter',
            logs='/private/var/log/ods-pixel-promoter',
            label='com.ods.pixel-native-promoter'):
    """Publish approved helper bytes under the caller's deployment lock.

    Expected hashes come from the selected ODS revision, not a candidate-supplied
    manifest. No source is executed as root during publication. Conflicting
    existing files are refused; identical partial publication can be replayed.
    Runtime directories, startup, readiness and rollback belong to orchestration.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    names = {'artifact_promoter.py', 'unix_peer.py', 'pixel_macos_custody.py'}
    if (type(sources) is not dict or type(expected_sha256) is not dict
            or set(sources) != names or set(expected_sha256) != names):
        raise ValueError('approved-promoter-source-set-required')
    for name in sorted(names):
        body, digest = sources[name], expected_sha256[name]
        if (type(body) is not bytes or not 0 < len(body) <= 1024 * 1024
                or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest)
                or hashlib.sha256(body).hexdigest() != digest):
            raise ValueError('approved-promoter-snapshot-required')
        compile(body, name, 'exec')
    rendered = render(python=python, program_root=program_root, workspace=workspace,
        owner=owner, state=state, runtime=runtime, logs=logs, label=label)
    entry = pwd.getpwnam(owner)
    if entry.pw_uid == 0:
        raise ValueError('nonroot-promoter-workspace-owner-required')
    program_root, definition = Path(program_root), Path(definition)
    if definition.name != label + '.plist':
        raise ValueError('promoter-definition-label-mismatch')
    helpers = operations_helpers()
    helpers.custody.protected_bytes(str(python), limit=128 * 1024 * 1024)
    installer = helpers.installer_helpers()
    files = [(program_root / name, sources[name]) for name in sorted(names)]
    files.extend([(program_root / 'promoter.sb', rendered['profile']), (definition, rendered['plist'])])
    for path, body in files:
        installer._preflight_file(path, body, mode=0o644, uid=0, gid=0)
    with helpers.custody.protected_directory(logs, create=True):
        pass
    for path, body in files:
        installer._write_exact(path, body, mode=0o644, uid=0, gid=0)
    return definition
