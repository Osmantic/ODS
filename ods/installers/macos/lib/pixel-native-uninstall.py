"""Retire one receipt-bound native Pixel deployment without destroying its state.

The protected access configuration binds the owner and ODS root. Retirement
quarantines fixed native paths only after all six launchd jobs are absent and
their observed process trees have exited. Unknown or transitional state fails
before stopping services. The dedicated Operations identity is retained.
"""
import argparse
import base64
import errno
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import pwd
import re
import stat
import subprocess
import sys
import time
import uuid

HERE = Path(__file__).resolve().parent
STATE = Path('/private/var/lib/ods-pixel-access')
SETTINGS = Path('/private/etc/ods/pixel-access.json')
BROKER = Path('/private/var/lib/pixel-ops-broker')
LABELS = ('native-gateway', 'access', 'access-relay', 'native-manager',
          'native-promoter', 'native-operations')
RETAIN = {'ops-identity.json', 'ops-identity.lock'}
PENDING = ('transition.json', 'policy-activation.json', 'runtime-upgrade.json')
DIRECTORIES = ('/private/var/lib/ods-pixel-native-config',
    '/private/var/lib/ods-pixel-access-probes', '/private/var/lib/ods-pixel-manager',
    '/private/var/lib/ods-pixel-artifact-promoter', '/private/var/run/ods-pixel-access',
    '/usr/local/libexec/ods-pixel-access', '/usr/local/libexec/ods-pixel-services',
    '/usr/local/libexec/ods-pixel-runtimes')
CONFIG_NAMES = ('pixel-access.json', 'pixel-access-relay.key', 'pixel-gateway.sb',
    'pixel-gateway.full-access.sb', 'pixel-gateway.sandboxed.sb', 'pixel-access-relay.sb')


def helper(name):
    spec = importlib.util.spec_from_file_location('retire_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def selected(settings, installation, services, *, owner, install_dir):
    """Validate independent protected records before consulting mutable state."""
    root = str(install_dir)
    if (settings.get('owner') != owner.pw_name or settings.get('install_dir') != root
            or settings.get('settings_data_dir') != root + '/data'
            or settings.get('state_dir') != str(STATE)
            or installation.get('owner') != owner.pw_uid
            or installation.get('phase') != 'active'
            or services.get('owner') != owner.pw_uid
            or services.get('progress', {}).get('phase') != 'services-active'
            or services.get('requiresRecovery')):
        raise ValueError('native-retirement-custody-mismatch')
    bundle = services.get('selection', {}).get('bundle')
    if (not isinstance(bundle, str) or not bundle.startswith(root + '/data/pixel-native/')
            or any(part in ('', '.', '..') for part in bundle.split('/')[1:])):
        raise ValueError('native-retirement-service-root-mismatch')
    return root


def state_name_allowed(name):
    return (name in RETAIN | {'installation.json', 'installation-edge.json', 'lock',
             'service-installation.json', 'service-baseline.json', 'verified.json', 'retirement.json'}
        or re.fullmatch(r'runtime-upgrade-[a-f0-9]{64}\.completed\.json', name)
        or re.fullmatch(r'runtime-upgrade-(?:context|edge)-[a-f0-9]{64}\.json', name)
        or re.fullmatch(r'runtime-upgrade-stop-[a-f0-9]{64}-(?:candidate|previous)-(?:access|gateway|manager|operations|promoter|relay)\.json', name)
        or re.fullmatch(r'runtime-controller-repair-[a-f0-9]{64}\.json', name))


def command(args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=30, check=False)


def prove_absent(target):
    return command(['/bin/launchctl', 'print', target]).returncode == 113


def verify_witness(value, *, owner, boot, hashes, targets):
    if (type(value) is not dict or value.get('schema') != 1 or value.get('owner') != owner
            or value.get('boot') != boot or value.get('hashes') != hashes
            or type(value.get('trees')) is not dict or set(value['trees']) != set(targets)):
        raise ValueError('native-retirement-stop-witness-mismatch')
    for tree in value['trees'].values():
        if (type(tree) is not list or not 1 <= len(tree) <= 4096
                or any(type(row) is not list or len(row) != 3
                    or any(type(n) is not int for n in row)
                    or row[0] <= 0 or row[1] <= 0 or not 0 <= row[2] < 1000000 for row in tree)):
            raise ValueError('native-retirement-stop-witness-invalid')
    return value['trees']


def retire(install_dir, owner_name, *, validate_only=False):
    if sys.platform != 'darwin' or os.geteuid() != 0:
        raise ValueError('native-retirement-macos-root-required')
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    import pixel_macos_custody as custody
    from pixel_macos_process import process_tree_snapshot, process_birth, ProcessIdentityError
    from pixel_gateway_service import launchd_definition_digest
    from pixel_access_bridge import atomic_json
    owner = pwd.getpwnam(owner_name)
    root = Path(install_dir)
    if (not root.is_absolute() or root == Path('/') or str(root) != str(root.resolve())
            or owner.pw_uid == 0 or root == Path(owner.pw_dir)):
        raise ValueError('native-retirement-install-root-invalid')
    residues = [Path(p) for p in DIRECTORIES] + [BROKER, STATE]
    residues += [Path('/private/etc/ods') / n for n in CONFIG_NAMES]
    plists = [Path('/Library/LaunchDaemons') / ('com.ods.pixel-' + n + '.plist') for n in LABELS]
    if not os.path.lexists(SETTINGS):
        if any(os.path.lexists(p) for p in residues + plists):
            # A previous successful retirement may retain only the identity.
            account = helper('pixel-native-ops-account')
            others = [p for p in residues + plists if p not in (STATE, BROKER)]
            if any(os.path.lexists(p) for p in others):
                raise ValueError('native-retirement-unbound-residue')
            account.verify_empty_home_only() if os.path.lexists(BROKER) else account.verify_identity_only()
        return {'status': 'absent'}
    with custody.protected_directory(STATE) as directory:
        lock = os.open('lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
        try:
            custody._verify_fd(lock, directory=False)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if any(os.path.lexists(STATE / n) for n in PENDING):
                raise ValueError('native-retirement-transition-pending')
            if any(not state_name_allowed(n) for n in os.listdir(directory)):
                raise ValueError('native-retirement-unknown-protected-state')
            def record(path):
                return json.loads(custody.protected_bytes(path, limit=32 * 1024 * 1024))
            settings = record(SETTINGS)
            installation = record(STATE / 'installation.json')
            services = record(STATE / 'service-installation.json')
            selected(settings, installation, services, owner=owner, install_dir=root)
            account = helper('pixel-native-ops-account')
            intent = account.validate_intent(record(STATE / 'ops-identity.json'))
            for kind in ('Groups', 'Users'):
                account.verify_record(account.read_record(kind), account.expected_attributes(intent, kind), complete=True)
            if os.path.lexists(BROKER):
                info = BROKER.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != intent['id'] or info.st_gid != intent['id']:
                    raise ValueError('native-retirement-broker-home-custody')
            # A protected root receipt owns the namespaces. Refuse symlink roots
            # and unexpected immutable-tree contents instead of adopting them.
            for name in DIRECTORIES:
                path = Path(name)
                if os.path.lexists(path):
                    info = path.lstat()
                    expected_uid = owner.pw_uid if path.name == 'ods-pixel-manager' else 0
                    if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022:
                        raise ValueError('native-retirement-directory-custody')
            runtime = Path('/usr/local/libexec/ods-pixel-runtimes')
            bundle = helper('pixel-runtime-bundle')
            for path in runtime.iterdir():
                if not re.fullmatch('[a-f0-9]{64}', path.name):
                    raise ValueError('native-retirement-unknown-runtime')
                custody.protected_tree_metadata(path)
                bundle.verify(path, expected_digest=path.name)
            config_root = Path('/private/var/lib/ods-pixel-native-config')
            if {p.name for p in config_root.iterdir()} != {str(owner.pw_uid)}:
                raise ValueError('native-retirement-foreign-config-owner')
            # Snapshot all root-controlled files used as retirement authority.
            snapshots = {str(p): custody.protected_bytes(p, limit=32 * 1024 * 1024)
                         for p in [SETTINGS, *plists, *(STATE / n for n in os.listdir(directory) if n != 'retirement.json')]}
            jobs = []
            for path in plists:
                definition = plistlib.loads(snapshots[str(path)])
                target = 'system/' + path.stem
                if definition.get('Label') != path.stem:
                    raise ValueError('native-retirement-job-label-mismatch')
                role = path.stem.removeprefix('com.ods.pixel-')
                expected_user = ('root' if role in ('access', 'native-promoter') else
                    '_ods_pixel_ops' if role == 'native-operations' else owner.pw_name)
                if definition.get('UserName') != expected_user:
                    raise ValueError('native-retirement-service-owner-mismatch')
                if role == 'native-gateway':
                    if (launchd_definition_digest(definition, ValueError) != settings.get('gateway_binding', {}).get('definition')
                            or 'HOME=' + str(root / 'data/pixel-native/home') not in definition.get('ProgramArguments', [])):
                        raise ValueError('native-retirement-gateway-binding-mismatch')
                if role in ('native-manager', 'native-promoter', 'native-operations'):
                    expected = services.get('recovery', {}).get('definitions', {}).get(role.removeprefix('native-'), {})
                    if (expected.get('path') != str(path)
                            or base64.b64decode(expected.get('body', ''), validate=True) != snapshots[str(path)]):
                        raise ValueError('native-retirement-service-definition-mismatch')
                result = command(['/bin/launchctl', 'print', target])
                if result.returncode == 113:
                    jobs.append((target, (), False))
                    continue
                if result.returncode:
                    raise ValueError('native-retirement-job-inspection-failed')
                custody.verify_loaded_launchd_definition(result.stdout, target, str(path), definition)
                pids = re.findall(r'^\s*pid = ([1-9][0-9]*)$', result.stdout, re.M)
                if len(pids) > 1 or not pids and not re.search(r'^\s*state = (?:not running|waiting|spawn scheduled)\s*$', result.stdout, re.M):
                    raise ValueError('native-retirement-process-unconfirmed')
                jobs.append((target, process_tree_snapshot(int(pids[0])) if pids else (), True))
            hashes = {path: hashlib.sha256(body).hexdigest() for path, body in snapshots.items()}
            boot_result = command(['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid'])
            if boot_result.returncode: raise ValueError('native-retirement-boot-identity-unavailable')
            boot = str(uuid.UUID(boot_result.stdout.strip()))
            witness_path = STATE / 'retirement.json'
            targets = [target for target, _, _ in jobs]
            if os.path.lexists(witness_path):
                trees = verify_witness(record(witness_path), owner=owner.pw_uid,
                    boot=boot, hashes=hashes, targets=targets)
                for target, tree, loaded in jobs:
                    if loaded and [list(row) for row in tree] != trees[target]:
                        raise ValueError('native-retirement-job-restarted')
                jobs = [(target, trees[target], loaded) for target, _, loaded in jobs]
            else:
                if any(not loaded or not tree for _, tree, loaded in jobs):
                    raise ValueError('native-retirement-stopped-job-needs-witness')
                trees = {target: [list(row) for row in tree] for target, tree, _ in jobs}
            if validate_only:
                return {'status': 'validated', 'owner': owner.pw_uid, 'installDir': str(root)}
            for path, body in snapshots.items():
                if custody.protected_bytes(path, limit=32 * 1024 * 1024) != body:
                    raise ValueError('native-retirement-authority-changed')
            if not os.path.lexists(witness_path):
                atomic_json(witness_path, {'schema': 1, 'owner': owner.pw_uid,
                    'boot': boot, 'hashes': hashes, 'trees': trees})
            for target, tree, loaded in jobs:
                if loaded and command(['/bin/launchctl', 'bootout', target]).returncode:
                    raise ValueError('native-retirement-stop-failed')
            deadline = time.monotonic() + 30
            while True:
                absent = all(prove_absent(target) for target, _, _ in jobs)
                survivors = []
                for _, tree, _ in jobs:
                    for pid, sec, usec in tree:
                        try:
                            if process_birth(pid) == (sec, usec): survivors.append(pid)
                        except ProcessIdentityError as error:
                            if getattr(error, 'errno', None) != errno.ESRCH: raise
                if absent and not survivors: break
                if time.monotonic() >= deadline: raise ValueError('native-retirement-processes-survive')
                time.sleep(0.25)
            for path, body in snapshots.items():
                if custody.protected_bytes(path, limit=32 * 1024 * 1024) != body:
                    raise ValueError('native-retirement-authority-changed-after-stop')
            archive = Path('/private/var/lib/ods-pixel-retired') / uuid.uuid4().hex
            with custody.protected_directory(archive, create=True): pass
            os.chmod(archive, 0o700)
            moves = []
            # The Operations account/home may be protected by macOS. Retain the
            # empty home and UUID-bound identity; preserve each former child.
            if os.path.lexists(BROKER):
                info = BROKER.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != intent['id'] or info.st_gid != intent['id']:
                    raise ValueError('native-retirement-broker-home-custody')
                moves.extend(BROKER.iterdir())
            moves += [p for p in residues + plists if p not in (STATE, BROKER) and os.path.lexists(p)]
            moves += [STATE / n for n in os.listdir(directory) if n not in RETAIN]
            receipt = {'schema': 1, 'owner': owner.pw_uid, 'installDir': str(root),
                       'status': 'retiring', 'moves': []}
            def save():
                temporary = archive / 'receipt.tmp'
                temporary.write_text(json.dumps(receipt, sort_keys=True) + '\n')
                os.chmod(temporary, 0o600)
                with temporary.open('rb') as stream: os.fsync(stream.fileno())
                os.replace(temporary, archive / 'receipt.json')
                directory_fd = os.open(archive, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try: os.fsync(directory_fd)
                finally: os.close(directory_fd)
            save()
            for index, source in enumerate(moves):
                destination = archive / ('item-' + str(index))
                receipt['moves'].append({'source': str(source), 'archive': str(destination), 'done': False})
                save()
                os.rename(source, destination)
                receipt['moves'][-1]['done'] = True
                save()
            receipt['status'] = 'retired'
            save()
            account.verify_empty_home_only() if os.path.lexists(BROKER) else account.verify_identity_only()
            return {'status': 'retired', 'archive': str(archive)}
        finally:
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--validate-only', action='store_true')
    args = parser.parse_args()
    try:
        print(json.dumps(retire(args.install_dir, args.owner, validate_only=args.validate_only)))
        return 0
    except Exception as error:
        print('Native Pixel retirement refused (' + type(error).__name__ + '): ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
