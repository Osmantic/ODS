"""Render the native Operations daemon and its policy-derived Seatbelt profile.

Publication must separately verify root custody of Python, broker, policy and
profile, and the selected source revision. Rendering never starts a job.
"""
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import subprocess
import sys


SPEC = importlib.util.spec_from_file_location('ops_seatbelt',
    Path(__file__).resolve().parents[3] / 'bin/pixel_macos_policy.py')
seatbelt = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seatbelt)
SPEC = importlib.util.spec_from_file_location('ops_service_custody',
    Path(__file__).resolve().parents[3] / 'bin/pixel_macos_custody.py')
custody = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(custody)


def installer_helpers():
    spec = importlib.util.spec_from_file_location('ops_protected_installer',
        Path(__file__).with_name('pixel-macos-access-install.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def select_python():
    """Discover the final Apple Python process, not its spawning launcher."""
    if sys.platform != 'darwin':
        raise ValueError('native-macos-python-required')
    probe = ('import ctypes,os; b=ctypes.create_string_buffer(4096); '
             'n=ctypes.CDLL(None).proc_pidpath(os.getpid(),b,len(b)); '
             'assert n>0; print(b.value.decode())')
    result = subprocess.run(['/usr/bin/python3', '-I', '-B', '-c', probe],
        cwd='/', env={'PATH': '/usr/bin:/bin', 'HOME': '/var/empty', 'TMPDIR': '/private/tmp'},
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
    if result.returncode or len(result.stdout) > 4096 or len(result.stdout.splitlines()) != 1:
        raise ValueError('native-python-discovery-failed')
    executable = seatbelt._path(result.stdout.strip())
    custody.protected_bytes(executable, limit=128 * 1024 * 1024)
    return Path(executable)


def render(*, identity, python, program_root, state, policy, label='com.ods.pixel-native-operations'):
    if (type(identity) is not dict or identity.get('name') != '_ods_pixel_ops'
            or any(type(identity.get(key)) is not int or identity[key] <= 0 for key in ('uid', 'gid'))
            or not re.fullmatch(r'com\.ods\.pixel-native-operations(?:\.[a-z0-9-]+)?', label)):
        raise ValueError('native-operations-service-identity-required')
    python, program_root, state = [Path(seatbelt._path(str(path))) for path in (python, program_root, state)]
    if state == program_root or state in program_root.parents or program_root in state.parents:
        raise ValueError('operations-code-state-overlap')
    readable = ['/Library/Developer']
    writable = [str(state)]
    if type(policy) is not dict or policy.get('schemaVersion') not in (1, 2):
        raise ValueError('validated-operations-policy-required')
    for target in policy.get('targets', {}).values():
        if target.get('enabled', True) and target.get('backend', 'ssh') == 'local':
            readable.extend(target.get('allowedRoots', []))
            writable.extend(target.get('writableRoots', []))
    if policy.get('download', {}).get('stagingRoot'):
        writable.append(policy['download']['stagingRoot'])
    # The same filesystem boundary implementation protects the native gateway.
    profile = seatbelt.render_policy(mode='sandboxed', writable=writable,
        protected=[str(program_root), str(python)], readable=readable,
        probe=str(state / 'runtime'), sockets=[])
    environment = {'HOME': str(state), 'USER': identity['name'], 'LOGNAME': identity['name'],
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'TMPDIR': str(state / 'runtime'),
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
    document = {
        'Label': label, 'UserName': identity['name'], 'GroupName': identity['name'],
        'ProgramArguments': ['/usr/bin/env', '-i',
            *(key + '=' + value for key, value in environment.items()),
            '/usr/bin/sandbox-exec', '-f', str(program_root / 'broker.sb'),
            str(python), '-I', '-B', str(program_root / 'broker.py'),
            '--policy', str(program_root / 'policy.json'), '--state', str(state)],
        'WorkingDirectory': str(state), 'RunAtLoad': True, 'KeepAlive': True,
        'ThrottleInterval': 10, 'ExitTimeOut': 20, 'Umask': 0o077,
        'ProcessType': 'Background', 'StandardInPath': '/dev/null',
        'StandardOutPath': str(state / 'runtime/broker.stdout.log'),
        'StandardErrorPath': str(state / 'runtime/broker.stderr.log'),
    }
    return {'profile': profile, 'plist': plistlib.dumps(document, sort_keys=True)}


def publication_files(*, broker_body, expected_broker_sha256, policy_body, identity, python,
            program_root, state, definition, label='com.ods.pixel-native-operations'):
    """Validate and render an approved broker snapshot without writing files.

    The orchestrator obtains the expected digest from its selected exact Pixel
    source revision, not from an untrusted manifest next to the candidate.
    Installation conflict checks and confined broker policy execution belong
    to publication, after the caller has journaled any managed replacement.
    """
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    if (type(broker_body) is not bytes or not 0 < len(broker_body) <= 16 * 1024 * 1024
            or not isinstance(expected_broker_sha256, str)
            or not re.fullmatch(r'[a-f0-9]{64}', expected_broker_sha256)
            or hashlib.sha256(broker_body).hexdigest() != expected_broker_sha256
            or type(policy_body) is not bytes or not 0 < len(policy_body) <= 1024 * 1024):
        raise ValueError('approved-operations-snapshot-required')
    policy = json.loads(policy_body)
    rendered = render(identity=identity, python=python, program_root=program_root,
                      state=state, policy=policy, label=label)
    broker = pwd.getpwnam(identity['name'])
    if (broker.pw_uid != identity['uid'] or broker.pw_gid != identity['gid']
            or broker.pw_shell != '/usr/bin/false'):
        raise ValueError('operations-account-identity-changed')
    program_root, definition = Path(program_root), Path(definition)
    if definition.name != label + '.plist':
        raise ValueError('operations-definition-label-mismatch')
    # Verify the executable itself, not just a directory containing a user binary.
    custody.protected_bytes(str(python), limit=128 * 1024 * 1024)
    files = [(program_root / 'broker.py', broker_body, 0o644, 0),
             (program_root / 'policy.json', policy_body, 0o640, identity['gid']),
             (program_root / 'broker.sb', rendered['profile'], 0o644, 0),
             (definition, rendered['plist'], 0o644, 0)]
    return files


def publish(**options):
    files = publication_files(**options)
    helpers = installer_helpers()
    for path, body, mode, gid in files:
        helpers._preflight_file(path, body, mode=mode, uid=0, gid=gid)
    for path, body, mode, gid in files[:-1]:
        helpers._write_exact(path, body, mode=mode, uid=0, gid=gid)
    validate_published_policy(identity=options['identity'], python=options['python'],
        program_root=options['program_root'], state=options['state'])
    path, body, mode, gid = files[-1]
    helpers._write_exact(path, body, mode=mode, uid=0, gid=gid)
    return Path(options['definition'])


def validate_published_policy(*, identity, python, program_root, state):
    """Run the existing policy validator confined to the broker identity."""
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('macos-root-required')
    program_root = Path(program_root)
    validation = ('import runpy,sys; from pathlib import Path; '
        'ns=runpy.run_path(sys.argv[1],run_name="ods_ops_validation"); '
        'ns["validate_policy"](ns["read_regular_json"](Path(sys.argv[2]),ns["MAX_POLICY_BYTES"]))')
    result = subprocess.run(['/usr/bin/sandbox-exec', '-f', str(program_root / 'broker.sb'),
        str(python), '-I', '-B', '-c', validation, str(program_root / 'broker.py'),
        str(program_root / 'policy.json')], user=identity['uid'], group=identity['gid'],
        extra_groups=[], cwd='/', env={'PATH': '/usr/bin:/bin', 'HOME': str(state),
        'TMPDIR': str(Path(state) / 'runtime')}, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=30)
    if result.returncode != 0:
        raise ValueError('native-operations-policy-validation-failed')
