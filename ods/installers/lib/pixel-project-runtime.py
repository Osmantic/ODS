"""Provision the fixed project executor without modifying Portal permissions.

The calling installer owns service start/health and only then enables the tool.
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
import tempfile

spec = importlib.util.spec_from_file_location('inspection_install', Path(__file__).with_name('pixel-preview-inspection.py'))
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

PROGRAM_ROOT = Path('/usr/local/libexec/ods-pixel-project')
UNIT = Path('/etc/systemd/system/ods-pixel-project.service')
CONFIG = Path('/etc/ods-pixel-project.json')
STATE = '/var/lib/ods-pixel-project'
FILES = ('project_service.py', 'project_controller.py', 'project_runtime.py', 'project_runtime_protocol.py',
         'project_capabilities.py', 'project_owner_recovery.py', 'project_owner_resolution.py',
         'project_storage.py',
         'project_diagnostics.py',
         'project_snapshot.py', 'project_jobs.py', 'project_artifacts.py', 'project_dispatch.py',
         'project_transport.py', 'project_authority.py', 'unix_peer.py')


def validate_config(value):
    if (not isinstance(value, dict) or set(value) - {'pythonImageId', 'storageLimits'} != {'ownerUid', 'imageId', 'workspace'}
            or type(value['ownerUid']) is not int or value['ownerUid'] <= 0
            or not isinstance(value['imageId'], str) or not re.fullmatch(r'sha256:[a-f0-9]{64}', value['imageId'])):
        raise ValueError('invalid project deployment')
    if 'pythonImageId' in value and (not isinstance(value['pythonImageId'], str)
            or not re.fullmatch(r'sha256:[a-f0-9]{64}', value['pythonImageId'])):
        raise ValueError('invalid Python project deployment')
    limits = value.get('storageLimits')
    if 'storageLimits' in value:
        if (type(limits) is not dict or set(limits) != {'jobBytes', 'totalBytes', 'maxJobs'}
                or any(type(v) is not int for v in limits.values())
                or not 32 * 1024**2 <= limits['jobBytes'] <= 4096 * 1024**2
                or not limits['jobBytes'] <= limits['totalBytes'] <= 8192 * 1024**2
                or not 1 <= limits['maxJobs'] <= 8):
            raise ValueError('invalid project storage policy')
    owner = pwd.getpwuid(value['ownerUid'])
    if (not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_-]*', owner.pw_name)
            or value['workspace'] != str(Path(owner.pw_dir) / '.openclaw/workspace-pixel')
            or any(c in value['workspace'] for c in '\n\r\x00')):
        raise ValueError('invalid project owner workspace')
    return owner


def _unit_bytes(config, *, previous_task_budget=False):
    owner = validate_config(config)
    # systemd expands percent specifiers even inside quotes.
    workspace = config['workspace'].replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    python_argument = (' --python-image ' + config['pythonImageId']) if 'pythonImageId' in config else ''
    storage_arguments = ''
    if 'storageLimits' in config:
        limits = config['storageLimits']
        storage_arguments = (f" --storage-bytes {limits['jobBytes']} --storage-total-bytes {limits['totalBytes']}"
                             f" --storage-max-jobs {limits['maxJobs']}")
    task_budget = 'TasksMax=64' if previous_task_budget else (
        '# Docker info discovers CLI plugins concurrently. Keep room for their threads\n'
        '# plus the single executor, capability probe and serialized owner connection.\n'
        'TasksMax=128')
    return f'''[Unit]
Description=ODS Portal isolated project executor
After=docker.service

[Service]
Type=simple
User={owner.pw_name}
ExecStart=/usr/bin/python3 -B {PROGRAM_ROOT}/project_service.py --workspace "{workspace}" --state-root {STATE} --image {config['imageId']}{python_argument}{storage_arguments}
Environment=PATH=/usr/local/bin:/usr/bin:/bin
Restart=on-failure
RestartSec=5
TimeoutStopSec=300
StateDirectory=ods-pixel-project
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths="{workspace}" {STATE}
RestrictAddressFamilies=AF_UNIX
CapabilityBoundingSet=
MemoryMax=768M
{task_budget}

[Install]
WantedBy=multi-user.target
'''.encode()


def unit_bytes(config):
    return _unit_bytes(config)


def build_config(source, owner_uid):
    owner = pwd.getpwuid(owner_uid)
    if os.getuid() != owner_uid or owner_uid == 0:
        raise ValueError('build as the workspace owner')
    # Validate both image definitions before doing work; no project files enter
    # this trusted build context and the model cannot supply an image reference.
    definitions = {key: common.source_bytes(Path(source) / name, owner_uid) for key, name in (
        ('imageId', 'Dockerfile.project-node'), ('pythonImageId', 'Dockerfile.project-python'))}
    images = {}
    for key, definition in definitions.items():
        with tempfile.TemporaryDirectory(prefix='ods-project-image-') as directory:
            root = Path(directory)
            (root / 'Dockerfile').write_bytes(definition)
            image_file = root / 'image-id'
            subprocess.run(['/usr/bin/docker', 'build', '--iidfile', str(image_file), str(root)],
                           check=True, stdout=sys.stderr, timeout=600)
            images[key] = image_file.read_text().strip()
    config = {'ownerUid': owner_uid, **images,
              'workspace': str(Path(owner.pw_dir) / '.openclaw/workspace-pixel')}
    validate_config(config)
    return config


def check_existing_owner(config):
    if os.geteuid() != 0 or sys.platform != 'linux':
        raise ValueError('Linux root installer required')
    validate_config(config)
    if os.path.lexists(CONFIG):
        common.protected_parent(CONFIG.parent)
        previous = json.loads(common.protected_file(CONFIG))
        validate_config(previous)
        if previous['ownerUid'] != config['ownerUid']:
            raise ValueError('project upgrade owner mismatch')
        if os.path.lexists(UNIT):
            common.protected_parent(UNIT.parent)
            # Recognize only the exact previously generated 64-task unit.
            # Owner, paths, both immutable images and all hardening settings
            # remain bound to the protected previous configuration. Never
            # normalize, remove directives from, or execute the installed unit.
            if common.protected_file(UNIT) not in (
                    unit_bytes(previous), _unit_bytes(previous, previous_task_budget=True)):
                raise ValueError('project service identity mismatch')
    elif os.path.lexists(UNIT) or os.path.lexists(PROGRAM_ROOT):
        raise ValueError('project installation identity missing')


def install_linux(source, config):
    check_existing_owner(config)
    # Capture and validate every input before writing any installed file.
    files = {PROGRAM_ROOT / name: common.source_bytes(Path(source) / name, config['ownerUid']) for name in FILES}
    files[UNIT] = unit_bytes(config)
    files[CONFIG] = (json.dumps(config, sort_keys=True) + '\n').encode()
    state = subprocess.run(['systemctl', 'is-active', '--quiet', UNIT.name],
                           capture_output=True, timeout=15)
    if state.returncode not in (3, 4):
        raise ValueError('stop project service before updating runtime files')
    for path in files:
        common.protected_parent(path.parent, create=True)
        if os.path.lexists(path):
            common.protected_file(path)
    # Earlier installs inherited restrictive umasks for this root-owned code
    # directory. Repair only our validated runtime root, after the full input
    # and installed-file preflight; the owner service must be able to traverse it.
    PROGRAM_ROOT.chmod(0o755)
    for path, body in files.items():
        fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(body)
                os.fchmod(stream.fileno(), 0o644)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)


def cleanup_linux(source, owner_uid, *, remove=False):
    """Validate the complete installed file set before any removal.

    Keep job state, workspace outputs and Docker caches. The caller stops and
    disables the service before requesting removal; this helper checks stopped
    state too, and never runs a recursive delete.
    """
    if os.geteuid() != 0 or sys.platform != 'linux':
        raise ValueError('Linux root installer required')
    for parent in (PROGRAM_ROOT.parent, UNIT.parent, CONFIG.parent):
        common.protected_parent(parent)
    present = [path for path in (CONFIG, UNIT, *(PROGRAM_ROOT / name for name in FILES)) if os.path.lexists(path)]
    if not present:
        return []
    if not os.path.lexists(CONFIG):
        raise ValueError('project installation identity missing; inspection required')
    config = json.loads(common.protected_file(CONFIG))
    validate_config(config)
    if config['ownerUid'] != owner_uid:
        raise ValueError('project installation owner mismatch')
    # An older or interrupted installation may never have published every
    # current runtime file. Prove each artifact we will remove against that
    # installation's source; never substitute a newer candidate's bytes.
    expected = {path: common.source_bytes(Path(source) / path.name, owner_uid)
                for path in present if path.parent == PROGRAM_ROOT}
    expected[UNIT] = unit_bytes(config)
    expected[CONFIG] = (json.dumps(config, sort_keys=True) + '\n').encode()
    if os.path.lexists(PROGRAM_ROOT):
        common.protected_parent(PROGRAM_ROOT)
        if set(PROGRAM_ROOT.iterdir()) - {PROGRAM_ROOT / name for name in FILES}:
            raise ValueError('unexpected project runtime files')
    for path in present:
        installed = common.protected_file(path)
        # Upgrades already recognize the exact former 64-task unit. Cleanup
        # must accept that same template bound to the protected configuration,
        # and pin its bytes for the immediate-before-unlink recheck below.
        if path == UNIT and installed == _unit_bytes(config, previous_task_budget=True):
            expected[UNIT] = installed
        if installed != expected[path]:
            raise ValueError('project installed source mismatch')
    if remove:
        state = subprocess.run(['systemctl', 'is-active', '--quiet', UNIT.name],
                               capture_output=True, timeout=15)
        if state.returncode not in (3, 4):
            raise ValueError('project service stop is not confirmed')
        for path in present:
            # Recheck each exact file immediately before unlinking it.
            if common.protected_file(path) != expected[path]:
                raise ValueError('project installed source changed during removal')
            path.unlink()
        if PROGRAM_ROOT.exists():
            PROGRAM_ROOT.rmdir()
    return [str(path) for path in present]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['build', 'check-install', 'install-linux', 'check-cleanup', 'cleanup-linux'])
    parser.add_argument('--source', required=True)
    parser.add_argument('--owner-uid', type=int)
    args = parser.parse_args()
    if args.action == 'build':
        print(json.dumps(build_config(args.source, args.owner_uid)))
    elif args.action in ('install-linux', 'check-install'):
        raw = sys.stdin.buffer.read(8193)
        if len(raw) > 8192:
            raise ValueError('deployment configuration too large')
        if args.action == 'check-install':
            check_existing_owner(json.loads(raw))
        else:
            install_linux(args.source, json.loads(raw))
    else:
        print(json.dumps(cleanup_linux(args.source, args.owner_uid, remove=args.action == 'cleanup-linux')))


if __name__ == '__main__':
    main()
