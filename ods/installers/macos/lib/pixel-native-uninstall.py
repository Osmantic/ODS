"""Retire one receipt-bound native Pixel deployment without destroying its state.

The protected access configuration binds the owner and ODS root. Retirement
quarantines fixed native paths only after all six launchd jobs are absent and
their observed process trees have exited, or an explicit preserved-plist reboot
barrier proves the old processes cannot survive. Unknown or transitional state fails
before stopping services. The dedicated Operations identity is retained.
An initial activation that failed, and whose rollback confirmed every stop, is
archived the same way without stopping anything.
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
import shutil
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
# A failed initial activation (pixel-macos-access-install.py): staging never
# starts a job, and rollback disables each started job and journals its stop.
INACTIVE_PHASES = ('staging', 'staging-failed', 'initial-stopped')
SERVICE_ORDER = ['manager', 'promoter', 'operations']
UNSTARTED_SERVICE_PHASES = ('starting', 'provisioning-identity', 'provisioning-state',
    'publishing-services', 'services-prepared')
STOPPED_SERVICE_PHASES = ('activation-failed', 'services-stopped')
HELP = '  Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC'
GUIDANCE = {
    'native-retirement-stopped-job-needs-witness':
        'Keep native state intact. Maintainer-assisted --prepare-stopped-recovery requires a Mac restart; '
        'see docs/MACOS_PIXEL_RETIREMENT_RECOVERY.md before using it.',
    'native-retirement-inactive-stop-unconfirmed':
        'This Pixel setup stopped before every Pixel service was confirmed stopped, so its files '
        'cannot be moved safely. Nothing was moved; keep them as they are.',
    'native-retirement-inactive-job-loaded':
        'A Pixel service from a setup that did not finish is loaded again. Nothing was moved; '
        'keep the files as they are.',
    'native-retirement-unbound-residue':
        'Native Pixel files from an earlier installation remain, but the record that ties them to '
        'an ODS installation is missing, so they cannot be proven safe to move. Nothing was moved.',
    'native-retirement-custody-mismatch':
        'The native Pixel on this Mac belongs to another ODS folder or user, or is in a state this '
        'uninstaller does not handle. Run it as the user who installed ODS, for the folder ODS was '
        'installed in. Nothing was moved.',
    'native-retirement-docker-command-failed':
        'Docker did not answer. Start Docker Desktop, wait until it is running, then run the same '
        'command again.',
    'operations-identity-process-active':
        'macOS background processes still run as the Pixel service account _ods_pixel_ops. '
        'Restart the Mac, then run the same command again.',
}


def helper(name):
    spec = importlib.util.spec_from_file_location('retire_' + name, HERE / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bound_owner(settings, installation, *, owner, root):
    if (settings.get('owner') != owner.pw_name or settings.get('install_dir') != root
            or settings.get('settings_data_dir') != root + '/data'
            or settings.get('state_dir') != str(STATE)
            or installation.get('owner') != owner.pw_uid):
        raise ValueError('native-retirement-custody-mismatch')


def bound_service_root(services, root):
    bundle = services.get('selection', {}).get('bundle')
    if (not isinstance(bundle, str) or not bundle.startswith(root + '/data/pixel-native/')
            or any(part in ('', '.', '..') for part in bundle.split('/')[1:])):
        raise ValueError('native-retirement-service-root-mismatch')


def selected(settings, installation, services, *, owner, install_dir):
    """Validate independent protected records before consulting mutable state."""
    root = str(install_dir)
    bound_owner(settings, installation, owner=owner, root=root)
    if (installation.get('phase') != 'active'
            or services.get('owner') != owner.pw_uid
            or services.get('progress', {}).get('phase') != 'services-active'
            or services.get('requiresRecovery')):
        raise ValueError('native-retirement-custody-mismatch')
    bound_service_root(services, root)
    return root


def selected_inactive(settings, installation, services, *, owner, install_dir):
    """Accept a failed initial activation only when none of its jobs can run.

    Staging never starts a job. A failed activation disables and unloads each
    job it started and journals its process tree as exited, leaving
    stopUnconfirmed empty. An interrupted or unconfirmed rollback, or any
    migration journal, remains a refusal.
    """
    root = str(install_dir)
    bound_owner(settings, installation, owner=owner, root=root)
    if (installation.get('schemaVersion') != 1
            or installation.get('operation') != 'initial-install'):
        raise ValueError('native-retirement-custody-mismatch')
    if installation.get('phase') not in INACTIVE_PHASES:
        raise ValueError('native-retirement-inactive-stop-unconfirmed')
    if services is None:
        return root
    if services.get('owner') != owner.pw_uid:
        raise ValueError('native-retirement-custody-mismatch')
    bound_service_root(services, root)
    progress, attempted = services.get('progress'), services.get('attempted')
    if (type(progress) is not dict or type(attempted) is not list
            or attempted != SERVICE_ORDER[:len(attempted)]):
        raise ValueError('native-retirement-inactive-stop-unconfirmed')
    if attempted:
        stopped = (progress.get('phase') in STOPPED_SERVICE_PHASES
                   and progress.get('attempted') == attempted
                   and progress.get('stopUnconfirmed') == [])
    else:
        # The journal is written before each bootstrap: none was attempted.
        stopped = progress.get('phase') in UNSTARTED_SERVICE_PHASES
    if not stopped:
        raise ValueError('native-retirement-inactive-stop-unconfirmed')
    return root


def state_name_allowed(name):
    # Model changes retain their backup and completion receipts after releasing
    # transition.json. Archive those records with the deployment, not as authority.
    return (name in RETAIN | {'installation.json', 'installation-edge.json', 'lock',
             'model-before.json', 'model-completed.json',
             'model-route-completed.json', 'model-promotion-completed.json',
             'service-installation.json', 'service-baseline.json', 'verified.json', 'retirement.json',
             'retirement-plists'}
        or re.fullmatch(r'runtime-upgrade-[a-f0-9]{64}\.completed\.json', name)
        or re.fullmatch(r'runtime-upgrade-(?:context|edge)-[a-f0-9]{64}\.json', name)
        or re.fullmatch(r'runtime-upgrade-stop-[a-f0-9]{64}-(?:candidate|previous)-(?:access|gateway|manager|operations|promoter|relay)\.json', name)
        or re.fullmatch(r'runtime-controller-repair-[a-f0-9]{64}\.json', name))


def validate_state_names(names):
    names = set(names)
    if names.intersection(PENDING):
        raise ValueError('native-retirement-transition-pending')
    if any(not state_name_allowed(name) for name in names):
        raise ValueError('native-retirement-unknown-protected-state')


def command(args):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, timeout=30, check=False)


def prove_absent(target):
    return command(['/bin/launchctl', 'print', target]).returncode == 113


def enable_ods_labels():
    """Clear launchd's disabled override on ODS labels whose plists are gone.

    A failed initial activation disables its jobs so they cannot start at
    boot, and the next install refuses a disabled label. A label is enabled
    only after its plist left /Library/LaunchDaemons. Only the exact disabled
    lines the installer itself recognizes are acted on.
    """
    result = command(['/bin/launchctl', 'print-disabled', 'system'])
    if result.returncode:
        raise ValueError('native-retirement-disabled-state-unavailable')
    lines = {line.strip() for line in result.stdout.splitlines()}
    for label in ('com.ods.pixel-' + name for name in LABELS):
        if '"' + label + '" => disabled' not in lines:
            continue
        if os.path.lexists(Path('/Library/LaunchDaemons') / (label + '.plist')):
            raise ValueError('native-retirement-disabled-job-definition-present')
        if command(['/bin/launchctl', 'enable', 'system/' + label]).returncode:
            raise ValueError('native-retirement-job-enable-failed')


def settle_operations_session(uid):
    """Stop macOS's own agents for the Operations account before moving its home.

    Once anything runs as _ods_pixel_ops, macOS starts its per-user agents
    (cfprefsd, distnoted, ...) in launchd domain user/<uid>. They outlive
    Pixel's jobs and keep writing Library/ into the account home (#7376).
    Callers verified the receipt-bound identity and that no Pixel job is
    loaded. The process list, not bootout's exit status, is the proof.
    """
    def running():
        result = command(['/bin/ps', '-axo', 'uid='])
        if result.returncode:
            raise ValueError('native-retirement-process-list-unavailable')
        return str(uid) in result.stdout.split()
    if not running():
        return
    command(['/bin/launchctl', 'bootout', 'user/' + str(uid)])
    deadline = time.monotonic() + 30
    while running():
        if time.monotonic() >= deadline:
            raise ValueError('operations-identity-process-active')
        time.sleep(0.25)


def verify_definition(path, body, *, settings, services, owner, root, digest):
    """Bind one protected plist to its role's account and the protected records."""
    definition = plistlib.loads(body)
    if definition.get('Label') != path.stem:
        raise ValueError('native-retirement-job-label-mismatch')
    role = path.stem.removeprefix('com.ods.pixel-')
    expected_user = ('root' if role in ('access', 'native-promoter') else
        '_ods_pixel_ops' if role == 'native-operations' else owner.pw_name)
    if definition.get('UserName') != expected_user:
        raise ValueError('native-retirement-service-owner-mismatch')
    if role == 'native-gateway':
        if (digest(definition, ValueError) != settings.get('gateway_binding', {}).get('definition')
                or 'HOME=' + str(root / 'data/pixel-native/home') not in definition.get('ProgramArguments', [])):
            raise ValueError('native-retirement-gateway-binding-mismatch')
    if role in ('native-manager', 'native-promoter', 'native-operations'):
        expected = (services.get('recovery') or {}).get('definitions', {}).get(role.removeprefix('native-'), {})
        if (expected.get('path') != str(path)
                or base64.b64decode(expected.get('body', ''), validate=True) != body):
            raise ValueError('native-retirement-service-definition-mismatch')
    return definition


def verify_native_directories(custody, owner, *, partial=False):
    """Refuse symlink roots and unexpected immutable-tree contents instead of adopting them.

    A failed activation can stop before creating the runtime or configuration root.
    """
    for name in DIRECTORIES:
        path = Path(name)
        if os.path.lexists(path):
            info = path.lstat()
            expected_uid = owner.pw_uid if path.name == 'ods-pixel-manager' else 0
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != expected_uid or info.st_mode & 0o022:
                raise ValueError('native-retirement-directory-custody')
    runtime = Path('/usr/local/libexec/ods-pixel-runtimes')
    if not partial or os.path.lexists(runtime):
        bundle = helper('pixel-runtime-bundle')
        for path in runtime.iterdir():
            if not re.fullmatch('[a-f0-9]{64}', path.name):
                raise ValueError('native-retirement-unknown-runtime')
            custody.protected_tree_metadata(path)
            bundle.verify(path, expected_digest=path.name)
    config_root = Path('/private/var/lib/ods-pixel-native-config')
    if not partial or os.path.lexists(config_root):
        owners = {p.name for p in config_root.iterdir()}
        if owners != {str(owner.pw_uid)} and not (partial and not owners):
            raise ValueError('native-retirement-foreign-config-owner')


def sandbox_selection(value, *, owner, root):
    """Bind Docker custody to the protected native gateway's exact mount roots."""
    base = str(root / 'data/pixel-native/home/.openclaw')
    mounts = value.get('Mounts', [])
    if not any(m.get('Type') == 'bind' and
            (m.get('Source') == base or m.get('Source', '').startswith(base + '/')) for m in mounts):
        return None
    cid = value.get('Id', '')
    labels = value.get('Config', {}).get('Labels') or {}
    if labels.get('openclaw.sandbox') != '1' and not value.get('Name', '').startswith('/pixel-sbx-'):
        return None  # Other ODS consumers can share the workspace bind.
    if (not re.fullmatch('[a-f0-9]{64}', cid)
            or labels.get('openclaw.sandbox') != '1'
            or labels.get('openclaw.sessionKey') != 'agent:pixel'
            or labels.get('org.osmantic.pixel.sandbox-uid') != str(owner.pw_uid)):
        raise ValueError('native-retirement-sandbox-owner-mismatch')
    expected = {'/workspace': (base + '/workspace-pixel', True),
                '/run/pixel-ods-control': (base + '/.ods-exec-control', False)}
    seen = set()
    for mount in mounts:
        dest, source = mount.get('Destination'), mount.get('Source', '')
        if dest in seen or mount.get('Type') != 'bind':
            raise ValueError('native-retirement-sandbox-mount-mismatch')
        seen.add(dest)
        if dest in expected:
            if (source, mount.get('RW')) != expected[dest]:
                raise ValueError('native-retirement-sandbox-mount-mismatch')
        elif (dest != '/workspace/.openclaw/sandbox-skills/skills'
                or not re.fullmatch(re.escape(base) + r'/sandbox/skills-workspaces/agent-pixel-[a-f0-9]+/\.openclaw/sandbox-skills/skills', source)
                or mount.get('RW') is not False):
            raise ValueError('native-retirement-sandbox-mount-mismatch')
    if not set(expected).issubset(seen):
        raise ValueError('native-retirement-sandbox-mount-mismatch')
    retired = '/ods-pixel-retired-' + cid[:16]
    name = value.get('Name', '')
    if name == retired and value.get('State', {}).get('Running') is False:
        return None  # An earlier retirement's preserved container is not live state.
    if not re.fullmatch(r'/pixel-sbx-agent-pixel-[a-f0-9]+', name):
        raise ValueError('native-retirement-sandbox-name-mismatch')
    return {'id': cid, 'name': name, 'retiredName': retired,
            'image': value.get('Image'), 'mounts': mounts, 'labels': labels,
            'inspect': value}


def mount_inventory(mounts):
    """Compare full mount objects by unique destination, not Docker list order."""
    if type(mounts) is not list:
        raise ValueError('native-retirement-sandbox-identity-changed')
    result = {}
    for mount in mounts:
        if (type(mount) is not dict or type(mount.get('Destination')) is not str
                or not mount['Destination'] or mount['Destination'] in result):
            raise ValueError('native-retirement-sandbox-identity-changed')
        result[mount['Destination']] = mount
    return result


class NativeSandboxes:
    def __init__(self, definition, *, owner, root):
        self.owner, self.root = owner, root
        arguments = definition.get('ProgramArguments', [])
        self.env = {'HOME': owner.pw_dir, 'PATH': '/usr/bin:/bin'}
        for key in ('DOCKER_HOST', 'DOCKER_CONFIG', 'PIXEL_HISTORY_DOCKER'):
            values = [x[len(key) + 1:] for x in arguments if x.startswith(key + '=')]
            if len(values) != 1: raise ValueError('native-retirement-docker-binding-missing')
            self.env[key] = values[0]
        self.binary = self.env.pop('PIXEL_HISTORY_DOCKER')
        if (not Path(self.binary).is_absolute() or
                not self.env['DOCKER_HOST'].startswith('unix:///') or
                self.env['DOCKER_CONFIG'] != str(root / 'data/pixel-native/home/docker-config')):
            raise ValueError('native-retirement-docker-binding-invalid')

    def call(self, *args):
        # Docker and its user-controlled configuration never execute as root.
        result = subprocess.run([self.binary, *args], cwd='/', env=self.env,
            user=self.owner.pw_uid, group=self.owner.pw_gid, extra_groups=[],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        if result.returncode or len(result.stdout) > 1024 * 1024:
            raise ValueError('native-retirement-docker-command-failed')
        return result.stdout

    def inspect(self, cid):
        values = json.loads(self.call('inspect', cid))
        if len(values) != 1 or values[0].get('Id') != cid:
            raise ValueError('native-retirement-sandbox-identity-changed')
        return values[0]

    def plan(self):
        ids = self.call('ps', '-aq', '--no-trunc').splitlines()
        if len(ids) > 256 or any(not re.fullmatch('[a-f0-9]{64}', cid) for cid in ids):
            raise ValueError('native-retirement-sandbox-inventory-invalid')
        result = []
        for cid in ids:
            selected = sandbox_selection(self.inspect(cid), owner=self.owner, root=self.root)
            if selected: result.append(selected)
        return result

    def prune_retired(self, keep):
        """Remove stopped sandboxes that earlier, completed retirements kept.

        Only this owner's exact Pixel sandbox under its retired name qualifies;
        the sandboxes this retirement just preserved are kept.
        """
        ids = self.call('ps', '-aq', '--no-trunc').splitlines()
        if len(ids) > 256 or any(not re.fullmatch('[a-f0-9]{64}', cid) for cid in ids):
            raise ValueError('native-retirement-sandbox-inventory-invalid')
        for cid in ids:
            if cid in keep:
                continue
            value = self.inspect(cid)
            labels = value.get('Config', {}).get('Labels') or {}
            if (value.get('Name') == '/ods-pixel-retired-' + cid[:16]
                    and value.get('State', {}).get('Running') is False
                    and labels.get('openclaw.sandbox') == '1'
                    and labels.get('openclaw.sessionKey') == 'agent:pixel'
                    and labels.get('org.osmantic.pixel.sandbox-uid') == str(self.owner.pw_uid)):
                self.call('rm', cid)

    def preserve(self, plans):
        for plan in plans:
            current = self.inspect(plan['id'])
            if (current.get('Image') != plan['image'] or mount_inventory(current.get('Mounts')) != mount_inventory(plan['mounts'])
                    or current.get('Config', {}).get('Labels') != plan['labels']
                    or current.get('Name') not in (plan['name'], plan['retiredName'])):
                raise ValueError('native-retirement-sandbox-identity-changed')
            if current['Name'] == plan['retiredName']:
                if current['State']['Running']:
                    raise ValueError('native-retirement-sandbox-restarted')
                continue
            self.call('stop', '--time', '10', plan['id'])
            if self.inspect(plan['id'])['State']['Running']:
                raise ValueError('native-retirement-sandbox-still-running')
            self.call('rename', plan['id'], plan['retiredName'].removeprefix('/'))
            current = self.inspect(plan['id'])
            if current['Name'] != plan['retiredName'] or current['State']['Running']:
                raise ValueError('native-retirement-sandbox-retirement-unconfirmed')


def prune_superseded_retirements(current):
    """Keep only the newest completed retirement archive.

    Each retirement moves the retired release's pinned runtimes (about
    0.5 GB), service definitions and access state into a new archive, and
    nothing reads an archive once its retirement completed: fleet Macs held 60
    archives (34 GB) after repeated reinstalls. Older archives whose own
    receipt records a completed retirement are removed; any other entry,
    including an interrupted retirement, is kept for inspection.
    """
    for entry in current.parent.iterdir():
        if entry == current or not re.fullmatch('[0-9a-f]{32}', entry.name):
            continue
        info = entry.lstat()
        # The helper runs as root, which owns every archive it creates.
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            continue
        try:
            value = json.loads((entry / 'receipt.json').read_text())
        except (OSError, ValueError):
            continue  # Missing or partial receipt: not provably complete.
        if isinstance(value, dict) and value.get('schema') == 1 and value.get('status') == 'retired':
            shutil.rmtree(entry)


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


def archive_moves(moves, *, custody, owner, root):
    """Rename each path into a new private archive, journaling every step."""
    archive = Path('/private/var/lib/ods-pixel-retired') / uuid.uuid4().hex
    with custody.protected_directory(archive, create=True): pass
    os.chmod(archive, 0o700)
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
    return archive


def retire_inactive(directory, record, *, custody, settings, installation, owner, root,
                    residues, plists, validate_only, digest):
    """Archive what a failed initial activation left, without stopping anything (#7071).

    Its rollback already disabled and unloaded the jobs it started and confirmed
    each stop, and the access bridge never created its lock. Bind the leftovers
    to the protected records and prove no job is loaded, then move them like
    normal retirement: nothing is deleted and the identity is retained. Admission
    stayed held throughout, so the gateway never started an agent sandbox.
    """
    names = set(os.listdir(directory))
    if names & {'retirement.json', 'retirement-plists'}:
        raise ValueError('native-retirement-custody-mismatch')
    services = (record(STATE / 'service-installation.json')
                if 'service-installation.json' in names else None)
    selected_inactive(settings, installation, services, owner=owner, install_dir=root)
    account = helper('pixel-native-ops-account')
    intent = None
    if 'ops-identity.json' in names:
        intent = account.validate_intent(record(STATE / 'ops-identity.json'))
        for kind in ('Groups', 'Users'):
            account.verify_record(account.read_record(kind), account.expected_attributes(intent, kind), complete=True)
    if os.path.lexists(BROKER):
        info = BROKER.lstat()
        if (intent is None or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != intent['id'] or info.st_gid != intent['id']):
            raise ValueError('native-retirement-broker-home-custody')
    verify_native_directories(custody, owner, partial=True)
    for name in names:
        custody.protected_bytes(STATE / name, limit=32 * 1024 * 1024)
    for path in plists:
        if os.path.lexists(path):
            verify_definition(path, custody.protected_bytes(path, limit=32 * 1024 * 1024),
                settings=settings, services=services or {}, owner=owner, root=root, digest=digest)
    def unloaded():
        for path in plists:
            result = command(['/bin/launchctl', 'print', 'system/' + path.stem])
            if result.returncode == 0:
                raise ValueError('native-retirement-inactive-job-loaded')
            if result.returncode != 113:
                raise ValueError('native-retirement-job-inspection-failed')
    unloaded()
    if validate_only:
        return {'status': 'validated', 'owner': owner.pw_uid, 'installDir': str(root)}
    if intent is not None:
        settle_operations_session(intent['id'])
        unloaded()
    moves = sorted(BROKER.iterdir()) if os.path.lexists(BROKER) else []
    moves += [p for p in residues + plists if p not in (STATE, BROKER) and os.path.lexists(p)]
    if intent is None:
        moves.append(STATE)  # No identity was provisioned, so nothing is retained.
    else:
        moves += [STATE / n for n in sorted(os.listdir(directory)) if n not in RETAIN]
    archive = archive_moves(moves, custody=custody, owner=owner, root=root)
    enable_ods_labels()
    if intent is not None:
        account.verify_empty_home_only() if os.path.lexists(BROKER) else account.verify_identity_only()
    elif os.path.lexists(STATE) or os.path.lexists(BROKER):
        raise ValueError('native-retirement-unbound-residue')
    return {'status': 'retired', 'archive': str(archive)}


def settle_retained_identity(account, custody, *, owner, root):
    """Clear what blocks reuse of a retained Operations identity (#7376).

    macOS's per-user agents for the account can outlive Pixel's jobs and write
    Library/ into its otherwise empty home, and an interrupted retirement can
    leave ODS's launchd labels disabled; the next install refuses both. Touch
    only the receipt-bound account and ODS's labels, and move the home's
    contents into a private archive instead of deleting them.
    """
    with custody.protected_directory(STATE) as directory:
        if set(os.listdir(directory)) != RETAIN:
            raise ValueError('operations-identity-not-only-protected-state')
    intent = account.validate_intent(json.loads(custody.protected_bytes(STATE / 'ops-identity.json', limit=4096)))
    for kind in ('Groups', 'Users'):
        account.verify_record(account.read_record(kind), account.expected_attributes(intent, kind), complete=True)
    if not all(prove_absent('system/com.ods.pixel-' + label) for label in LABELS):
        raise ValueError('operations-identity-service-loaded-or-ambiguous')
    settle_operations_session(intent['id'])
    if os.path.lexists(BROKER):
        info = BROKER.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != intent['id'] or info.st_gid != intent['id']:
            raise ValueError('native-retirement-broker-home-custody')
        leftovers = sorted(BROKER.iterdir())
        if leftovers:
            archive_moves(leftovers, custody=custody, owner=owner, root=root)
    enable_ods_labels()


def retire(install_dir, owner_name, *, validate_only=False,
           prepare_stopped_recovery=False, resume_stopped_recovery=False):
    if sum(bool(value) for value in (validate_only, prepare_stopped_recovery, resume_stopped_recovery)) > 1:
        raise ValueError('native-retirement-conflicting-modes')
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
    plain = not (validate_only or prepare_stopped_recovery or resume_stopped_recovery)
    if not os.path.lexists(SETTINGS):
        if any(os.path.lexists(p) for p in residues + plists):
            # A previous successful retirement may retain only the identity.
            account = helper('pixel-native-ops-account')
            others = [p for p in residues + plists if p not in (STATE, BROKER)]
            if any(os.path.lexists(p) for p in others):
                raise ValueError('native-retirement-unbound-residue')
            if plain and os.path.lexists(STATE):
                settle_retained_identity(account, custody, owner=owner, root=root)
            account.verify_empty_home_only() if os.path.lexists(BROKER) else account.verify_identity_only()
        return {'status': 'absent'}
    with custody.protected_directory(STATE) as directory:
        # The access bridge creates this mutex on its first transition; an
        # initial activation that failed before that never did (#7071).
        lock = os.open('lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            custody._verify_fd(lock, directory=False)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            validate_state_names(os.listdir(directory))
            def record(path):
                return json.loads(custody.protected_bytes(path, limit=32 * 1024 * 1024))
            installation = record(STATE / 'installation.json')
            if installation.get('phase') != 'active':
                if prepare_stopped_recovery or resume_stopped_recovery:
                    raise ValueError('native-retirement-inactive-uses-plain-retirement')
                return retire_inactive(directory, record, custody=custody, settings=record(SETTINGS),
                    installation=installation, owner=owner, root=root, residues=residues,
                    plists=plists, validate_only=validate_only, digest=launchd_definition_digest)
            reboot = helper('pixel-native-retirement-reboot').Recovery(STATE, plists,
                read=lambda path: custody.protected_bytes(path, limit=32 * 1024 * 1024),
                directory=custody.protected_directory, save=atomic_json,
                command=command, absent=prove_absent)
            if resume_stopped_recovery and reboot.document is None:
                raise ValueError('native-retirement-reboot-preparation-required')
            settings = record(SETTINGS)
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
            # A protected root receipt owns the namespaces.
            verify_native_directories(custody, owner)
            # Snapshot all root-controlled files used as retirement authority.
            snapshots = {str(p): custody.protected_bytes(reboot.path(p), limit=32 * 1024 * 1024)
                         for p in [SETTINGS, *plists, *(STATE / n for n in os.listdir(directory)
                             if n not in ('retirement.json', 'retirement-plists'))]}
            gateway = plistlib.loads(snapshots[str(plists[0])])
            if launchd_definition_digest(gateway, ValueError) != settings.get('gateway_binding', {}).get('definition'):
                raise ValueError('native-retirement-gateway-binding-mismatch')
            sandboxes = NativeSandboxes(gateway, owner=owner, root=root)
            sandbox_plans = sandboxes.plan()
            jobs = []
            for path in plists:
                definition = verify_definition(path, snapshots[str(path)], settings=settings,
                    services=services, owner=owner, root=root, digest=launchd_definition_digest)
                target = 'system/' + path.stem
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
            if prepare_stopped_recovery:
                if reboot.document is None and all(loaded and tree for _, tree, loaded in jobs):
                    raise ValueError('native-retirement-running-jobs-use-normal-retirement')
                return reboot.prepare(owner=owner.pw_uid, boot=boot, hashes=hashes, snapshots=snapshots)
            if reboot.document is not None:
                if not (resume_stopped_recovery or validate_only):
                    raise ValueError('native-retirement-use-resume-stopped-recovery')
                trees = reboot.verify_reboot(owner=owner.pw_uid, boot=boot,
                    hashes=hashes, snapshots=snapshots)
                jobs = [(target, (), False) for target in targets]
            elif os.path.lexists(witness_path):
                witness = record(witness_path)
                trees = verify_witness(witness, owner=owner.pw_uid,
                    boot=boot, hashes=hashes, targets=targets)
                prior = witness.get('sandboxes')
                if not isinstance(prior, list) or not {p['id'] for p in sandbox_plans}.issubset({p['id'] for p in prior}):
                    raise ValueError('native-retirement-sandbox-witness-mismatch')
                sandbox_plans = prior
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
            reboot.unchanged(snapshots)
            if not os.path.lexists(witness_path):
                atomic_json(witness_path, {'schema': 1, 'owner': owner.pw_uid,
                    'boot': boot, 'hashes': hashes, 'trees': trees, 'sandboxes': sandbox_plans})
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
            # Bind mounts retain old directory inodes after --force removes the
            # install root. Preserve the container, but vacate its live name and
            # stop its processes before moving any native or workspace paths.
            sandboxes.preserve(sandbox_plans)
            reboot.unchanged(snapshots, code='native-retirement-authority-changed-after-stop')
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
            archive = archive_moves(moves, custody=custody, owner=owner, root=root)
            account.verify_empty_home_only() if os.path.lexists(BROKER) else account.verify_identity_only()
            # This retirement is complete and is now the recovery record;
            # earlier completed ones and their stopped sandboxes are only disk.
            sandboxes.prune_retired({plan['id'] for plan in sandbox_plans})
            prune_superseded_retirements(archive)
            return {'status': 'retired', 'archive': str(archive)}
        finally:
            os.close(lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-dir', required=True)
    parser.add_argument('--owner', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--validate-only', action='store_true')
    mode.add_argument('--prepare-stopped-recovery', action='store_true',
        help='Preserve autostart plists and unload verified jobs; requires an explicit Mac reboot before retirement')
    mode.add_argument('--resume-stopped-recovery', action='store_true',
        help='After that reboot, verify preserved authority and retire the native deployment')
    args = parser.parse_args()
    try:
        print(json.dumps(retire(args.install_dir, args.owner, validate_only=args.validate_only,
            prepare_stopped_recovery=args.prepare_stopped_recovery,
            resume_stopped_recovery=args.resume_stopped_recovery)))
        return 0
    except Exception as error:
        print('Native Pixel retirement refused (' + type(error).__name__ + '): ' + str(error), file=sys.stderr)
        print(GUIDANCE.get(str(error), 'Keep the remaining Pixel files as they are. When you ask for help, '
              'share only the reason above, never private receipts, .env or keys.'), file=sys.stderr)
        print(HELP, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
