"""Retire one receipt-bound native Pixel deployment without destroying its state.

The protected access configuration binds the owner and ODS root. Retirement
quarantines fixed native paths only after the selected six or seven jobs are absent and
their observed process trees have exited, or an explicit preserved-plist reboot
barrier proves the old processes cannot survive. Unknown or transitional state fails
before stopping services. The dedicated Operations identity is retained.
"""
import argparse
from contextlib import contextmanager
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
INTEGRATION_LABEL = 'native-integrations'
INTEGRATION_DIRECTORIES = ('/private/var/lib/ods-pixel-integrations',
    '/private/var/lib/ods-pixel-integrations-auth', '/private/var/log/ods-pixel-integrations')
RETAIN = {'ops-identity.json', 'ops-identity.lock'}
PENDING = ('transition.json', 'policy-activation.json', 'runtime-upgrade.json')
DIRECTORIES = ('/private/var/lib/ods-pixel-native-config',
    '/private/var/lib/ods-pixel-access-probes', '/private/var/lib/ods-pixel-manager',
    '/private/var/lib/ods-pixel-artifact-promoter', '/private/var/run/ods-pixel-access',
    '/usr/local/libexec/ods-pixel-access', '/usr/local/libexec/ods-pixel-services',
    '/usr/local/libexec/ods-pixel-runtimes')
CONFIG_NAMES = ('pixel-access.json', 'pixel-access-relay.key', 'pixel-gateway.sb',
    'pixel-gateway.full-access.sb', 'pixel-gateway.sandboxed.sb', 'pixel-access-relay.sb')
# Source data remains outside the legacy retirement family until the typed
# withdrawal/inventory transaction qualifies. Its presence must never yield an
# "absent" result merely because the access configuration has disappeared.
SOURCE_RESIDUES = ('/private/var/lib/pixel-source-broker',
    '/private/var/log/pixel-source-broker', '/private/etc/pixel-source-broker.env',
    '/usr/local/libexec/ods-pixel-access/pixel_connections/source-broker-kit',
    '/private/etc/ods/pixel-provider-runtime.json',
    '/private/etc/ods/pixel-provider-runtime-manifest.json')


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
    # Model changes retain their backup and completion receipts after releasing
    # transition.json. Archive those records with the deployment, not as authority.
    return (name in RETAIN | {'installation.json', 'installation-edge.json', 'lock',
             'model-before.json', 'model-completed.json',
             'model-route-completed.json', 'model-promotion-completed.json',
             'service-installation.json', 'service-baseline.json', 'verified.json', 'retirement.json',
             'retirement-plists', 'integrations-preservation.json', '.integrations-preservation.pending'}
        or re.fullmatch(r'integrations-preservation-interrupted-[a-f0-9]{64}\.json', name)
        or re.fullmatch(r'runtime-upgrade-[a-f0-9]{64}\.completed\.json', name)
        or re.fullmatch(r'runtime-upgrade-(?:context|edge)-[a-f0-9]{64}\.json', name)
        or re.fullmatch(r'runtime-upgrade-stop-[a-f0-9]{64}-(?:candidate|previous)-(?:access|gateway|integrations|manager|operations|promoter|relay)\.json', name)
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


def integration_disabled():
    result = command(['/bin/launchctl', 'print-disabled', 'system'])
    lines = result.stdout.strip().splitlines()
    if result.returncode or not lines or lines[0].strip() != 'disabled services = {' or lines[-1].strip() != '}':
        raise ValueError('native-retirement-disabled-state-unavailable')
    entries = {}
    for line in lines[1:-1]:
        match = re.fullmatch(r'\s*"([^"\n]+)" => (enabled|disabled)\s*', line)
        if not match or match[1] in entries:
            raise ValueError('native-retirement-disabled-state-unavailable')
        entries[match[1]] = match[2] == 'disabled'
    return entries.get('com.ods.pixel-native-integrations', False)


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


def _source_retirement_intent(value, *, owner_uid):
    """Pure schema validation, never Source custody or archive authority."""
    fields = {'schemaVersion', 'kind', 'nonce', 'owner', 'runtime', 'inventory'}
    if (type(value) is not dict or set(value) != fields or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != 'ods-native-source-retirement-intent'
            or type(value['nonce']) is not str or not re.fullmatch('[a-f0-9]{32}', value['nonce'])):
        raise ValueError('native-source-retirement-intent-invalid')
    selected = value['owner']
    if (type(selected) is not dict or set(selected) != {'uid', 'gid', 'name', 'installDir'}
            or type(selected['uid']) is not int or selected['uid'] != owner_uid or owner_uid <= 0
            or type(selected['gid']) is not int or selected['gid'] < 0
            or type(selected['name']) is not str or not selected['name']
            or any(ord(char) < 32 or ord(char) == 127 for char in selected['name'])
            or type(selected['installDir']) is not str or not selected['installDir'].startswith('/')
            or any(ord(char) < 32 or ord(char) == 127 for char in selected['installDir'])
            or any(part in ('', '.', '..') for part in selected['installDir'].split('/')[1:])):
        raise ValueError('native-source-retirement-owner-invalid')
    try:
        owner = pwd.getpwuid(owner_uid)
    except KeyError:
        raise ValueError('native-source-retirement-owner-invalid') from None
    if (selected['name'], selected['gid']) != (owner.pw_name, owner.pw_gid) or selected['installDir'] == owner.pw_dir:
        raise ValueError('native-source-retirement-owner-invalid')
    from pixel_connections import custody
    try:
        custody.native_retirement_inventory_receipt(value['inventory'], owner_uid=owner_uid,
            owner_gid=selected['gid'], runtime=value['runtime'])
    except custody.CustodyError:
        raise ValueError('native-source-retirement-inventory-invalid') from None
    return value


def source_retirement_witness(core, source):
    """Freeze a distinct Source witness without mutating the existing Core one.

    Actual inventory must come from the qualified old Root selection. This
    serializer performs no Source inspection, quiescence, write or adoption.
    """
    if (type(core) is not dict or set(core) != {'schema', 'owner', 'boot', 'hashes', 'trees', 'sandboxes'}
            or type(core['schema']) is not int or core['schema'] != 1):
        raise ValueError('native-source-retirement-core-witness-invalid')
    source = _source_retirement_intent(source, owner_uid=core['owner'])
    value = dict(core, schema=3, kind='ods-native-source-installer-retirement', source=source)
    verify_witness(value, owner=core['owner'], boot=core['boot'], hashes=core['hashes'], targets=list(core['trees']))
    return value


def source_retirement_transaction(raw):
    """Identify exact protected witness bytes; no candidate or mutation grant."""
    if type(raw) is not bytes or not 0 < len(raw) <= 32 * 1024 * 1024:
        raise ValueError('native-source-retirement-witness-invalid')
    def unique(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValueError('native-source-retirement-witness-ambiguous')
            result[name] = value
        return result
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=unique)
        if type(value) is not dict or value.get('schema') != 3:
            raise ValueError('native-source-retirement-witness-invalid')
        verify_witness(value, owner=value['owner'], boot=value['boot'], hashes=value['hashes'], targets=list(value['trees']))
    except (KeyError, TypeError, UnicodeError, RecursionError):
        raise ValueError('native-source-retirement-witness-invalid') from None
    return {'kind': 'native-installer-retirement', 'transaction': hashlib.sha256(raw).hexdigest(),
        'currentDigest': value['source']['runtime']['bundleSha256']}


def _source_retirement_bridge(settings, owner):
    """Construct the actual protected old controller; never infer a candidate.

    The caller must already hold the existing controller flock, and enter this
    bridge's bounded scope before obtaining Source custody or quiescing roles.
    The key is the existing owner controller key, not a Source refresh token.
    """
    access = helper('pixel-macos-access-install')
    path = access._destination(access.ACCESS_FILES['key'])
    key = access._configuration_bytes(path, owner.pw_uid)
    return access._bridge_from_recovery_plan({'access_settings': settings, 'key': key},
        owner_name=owner.pw_name)


def _legacy_source_descriptor_proof(bridge):
    """Known old Core with public descriptors and provably no Source family.

    This is a distinct route, never a fallback after a Source inspection error.
    It creates no account, kit, witness, grant or execution capability.
    """
    from pixel_connections import custody as source
    from pixel_provider.runtime_custody import RuntimeCustody
    runtime, checksum = RuntimeCustody(bridge).inspect()
    root = Path(runtime['runtimeRoot']).parent
    if (root.parent != source.NATIVE_RUNTIMES or not re.fullmatch('[a-f0-9]{64}', root.name)
            or Path(runtime['runtimeRoot']) != root / 'runtime'):
        raise ValueError('native-retirement-legacy-runtime-unqualified')
    manifest, _digest = helper('pixel-runtime-bundle').verify(root, expected_digest=root.name)
    if ('native-source' in manifest.get('entries', {})
            or any(name.startswith('native-source/') for name in manifest.get('entries', {}))
            or os.path.lexists(root / 'native-source')):
        raise ValueError('native-retirement-source-capability-requires-typed-retirement')
    inspected = source.native_inspect_installer_absence(bridge, current_digest=root.name)
    if (inspected['oldRuntimeCustodySha256'] != checksum or inspected['state'][:3] != (None, {}, {'kind': 'absent'})
            or os.path.lexists(source.NATIVE_META / 'native-app-enrollment.json')):
        raise ValueError('native-retirement-legacy-source-state-preserved')
    return dict(runtime=runtime, custodySha256=checksum, state=inspected['state'])


def _verify_legacy_source_descriptor_proof(bridge, proof):
    """Same immutable old Core and fresh file-only absence after Core stop."""
    from pixel_connections import custody as source
    from pixel_provider.runtime_custody import RuntimeCustody
    runtime, checksum = RuntimeCustody(bridge).inspect()
    if (runtime != proof['runtime'] or checksum != proof['custodySha256']
            or source._native_installer_absence_state(bridge, qualified_runtime=runtime) != proof['state']
            or os.path.lexists(source.NATIVE_META / 'native-app-enrollment.json')):
        raise ValueError('native-retirement-legacy-source-state-changed')


def _prepare_source_retirement(bridge, owner, core):
    """Quiesce and withdraw the actual old Source before stopping its gateway.

    Caller holds the controller flock and this bridge's bounded scope. This is
    only the complete-v1 preparation boundary; no Source path is archived here.
    The caller must separately qualify its exact archive cursor before invoking
    this operation, and must retain the immutable witness after any failure.
    """
    from pixel_connections import custody
    from pixel_connections.source_backend import for_bridge
    from pixel_connections.native_executor import _publisher
    from pixel_connections import native_retirement
    from pixel_macos_custody import protected_bytes
    from pixel_access_bridge import atomic_json
    from pixel_access_bridge import LaunchdAccessBridge
    # The CLI enters the verified support capsule before reaching this live
    # bounded/locked scope. Ordinary discovery selects only the actual old
    # protected gateway/owner/config; candidate or caller-supplied fields never
    # initialize Source custody.
    LaunchdAccessBridge.discover(bridge)
    if bridge.state != STATE or (bridge.owner.pw_uid, bridge.owner.pw_gid) != (owner.pw_uid, owner.pw_gid):
        raise ValueError('native-source-retirement-controller-changed')
    backend = for_bridge(bridge, linux_paths=custody.Paths(), linux_run=custody.system_run)
    selected = backend.selection
    publisher = _publisher(selected)
    path = STATE / 'retirement.json'
    if os.path.lexists(path):
        raw = protected_bytes(path, limit=32 * 1024 * 1024)
        transaction = source_retirement_transaction(raw)
        value = json.loads(raw)
        if any(value[key] != core[key] for key in ('owner', 'boot', 'hashes', 'trees', 'sandboxes')):
            raise ValueError('native-source-retirement-core-witness-changed')
    else:
        # Disable both triggers and stop every recorded Source role while the
        # old actual gateway still provides its protected deployment lease.
        backend.quiesce_for_sign_in()
        selected = backend.selection
        publisher = _publisher(selected)
        _module, doc, _component, ref = publisher._material(selected)
        if os.path.lexists(custody.NATIVE_STATE):
            inventory = native_retirement.add_history(selected, custody.native_retirement_inventory(selected))
        else:
            inventory = native_retirement.code_only_inventory(selected)
        source = dict(schemaVersion=1, kind='ods-native-source-retirement-intent', nonce=os.urandom(16).hex(),
            owner=dict(uid=owner.pw_uid, gid=owner.pw_gid, name=owner.pw_name, installDir=str(bridge.install)),
            runtime=dict(deploymentSha256=selected.deployment.checksum, custodySha256=doc['runtimeCustodySha256'],
                bundleSha256=doc['bundleSha256'], sourceRef=ref), inventory=inventory)
        value = source_retirement_witness(core, source)
        if os.path.lexists(path):
            raise ValueError('native-source-retirement-witness-changed')
        atomic_json(path, value)
        raw = protected_bytes(path, limit=32 * 1024 * 1024)
        if json.loads(raw) != value:
            raise ValueError('native-source-retirement-witness-changed')
        transaction = source_retirement_transaction(raw)
    if value['source']['inventory']['kind'] == native_retirement.CODE_ONLY_KIND:
        # No fake generation, withdrawal receipt or new broker account exists.
        # Repeat actual code-only custody after the immutable witness is bound.
        if native_retirement.code_only_inventory(selected) != value['source']['inventory']:
            raise ValueError('native-source-retirement-code-only-changed')
        return {'transaction': transaction, 'witness': value, 'intent': None, 'receipt': None}
    if any(not callable(getattr(publisher, name, None)) for name in (
            'retirement_withdrawal_intent', 'withdraw_retirement_generation', 'verify_retirement_withdrawal')):
        raise ValueError('native-source-retirement-capability-required')
    intent = publisher.retirement_withdrawal_intent(selected, transaction=transaction)
    receipt = publisher.withdraw_retirement_generation(selected, transaction=transaction, intent=intent)
    if publisher.verify_retirement_withdrawal(selected, transaction=transaction, intent=intent, receipt=receipt) != receipt:
        raise ValueError('native-source-retirement-withdrawal-unconfirmed')
    if protected_bytes(path, limit=32 * 1024 * 1024) != raw:
        raise ValueError('native-source-retirement-witness-changed')
    return {'transaction': transaction, 'witness': value, 'intent': intent, 'receipt': receipt}


def verify_witness(value, *, owner, boot, hashes, targets):
    if (type(value) is not dict or type(value.get('schema')) is not int or value.get('schema') not in (1, 3) or value.get('owner') != owner
            or value.get('boot') != boot or value.get('hashes') != hashes
            or type(value.get('trees')) is not dict or set(value['trees']) != set(targets)):
        raise ValueError('native-retirement-stop-witness-mismatch')
    if value['schema'] == 3:
        if (set(value) != {'schema', 'kind', 'owner', 'boot', 'hashes', 'trees', 'sandboxes', 'source'}
                or value['kind'] != 'ods-native-source-installer-retirement'
                or type(value['owner']) is not int or value['owner'] <= 0
                or type(value['hashes']) is not dict or not 1 <= len(value['hashes']) <= 512
                or any(type(path) is not str or not path.startswith('/')
                    or any(ord(char) < 32 or ord(char) == 127 for char in path)
                    or any(part in ('', '.', '..') for part in path.split('/')[1:])
                    or type(checksum) is not str or not re.fullmatch('[a-f0-9]{64}', checksum)
                    for path, checksum in value['hashes'].items())
                or type(value['sandboxes']) is not list or len(value['sandboxes']) > 256):
            raise ValueError('native-source-retirement-witness-invalid')
        _source_retirement_intent(value['source'], owner_uid=owner)
        try:
            if (type(value['boot']) is not str or str(uuid.UUID(value['boot'])) != value['boot']
                    or value['source']['inventory']['boot'].lower() != value['boot']):
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError('native-source-retirement-boot-invalid') from None
    for tree in value['trees'].values():
        if (type(tree) is not list or not 1 <= len(tree) <= 4096
                or any(type(row) is not list or len(row) != 3
                    or any(type(n) is not int for n in row)
                    or row[0] <= 0 or row[1] <= 0 or not 0 <= row[2] < 1000000 for row in tree)):
            raise ValueError('native-retirement-stop-witness-invalid')
    return value['trees']


def _source_core_allowed(path, witness):
    """Only the existing fixed Core family, after Source is fully preserved."""
    original = os.fspath(path)
    path = Path(path)
    if (not path.is_absolute() or str(path) != original
            or any(part in ('', '.', '..') for part in original.split('/')[1:])
            or any(ord(char) < 32 or ord(char) == 127 for char in original)):
        return False
    if str(path) in (*DIRECTORIES, *INTEGRATION_DIRECTORIES, *SOURCE_RESIDUES[-2:]): return True
    if path.parent == SETTINGS.parent and path.name in CONFIG_NAMES: return True
    if path.parent == Path('/Library/LaunchDaemons') and 'system/' + path.stem in witness['trees']: return True
    if path.parent == BROKER:
        return bool(path.name and path.name not in ('.', '..') and not any(ord(c) < 32 for c in path.name))
    return (path.parent == STATE and path.name not in RETAIN | {'retirement.json', 'retirement-plists', 'lock'}
        and state_name_allowed(path.name))


@contextmanager
def _source_core_parent(path):
    import pixel_macos_custody as custody
    if Path(path) != BROKER:
        with custody.protected_directory(path) as directory: yield directory
        return
    account = helper('pixel-native-ops-account')
    identity = account.validate_intent(json.loads(custody.protected_bytes(STATE / 'ops-identity.json')))
    for kind in ('Groups', 'Users'):
        account.verify_record(account.read_record(kind), account.expected_attributes(identity, kind), complete=True)
    with custody.protected_directory(BROKER.parent) as parent:
        directory = os.open(BROKER.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(directory)
            if ((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (identity['id'], identity['id'], 0o750)
                    or os.stat(BROKER.name, dir_fd=parent, follow_symlinks=False).st_ino != info.st_ino):
                raise ValueError('native-source-retirement-operations-home-changed')
            yield directory
        finally: os.close(directory)


def _source_operations_absent(witness):
    """Fresh dedicated-UID census before preserving any remaining Core data."""
    import pixel_macos_custody as custody
    from pixel_connections import native_retirement as native
    account = helper('pixel-native-ops-account')
    raw = custody.protected_bytes(STATE / 'ops-identity.json', limit=4096)
    if hashlib.sha256(raw).hexdigest() != witness['hashes'].get(str(STATE / 'ops-identity.json')):
        raise ValueError('native-source-retirement-operations-identity-changed')
    identity = account.validate_intent(json.loads(raw))
    for kind in ('Groups', 'Users'):
        account.verify_record(account.read_record(kind), account.expected_attributes(identity, kind), complete=True)
    seen = set()
    for line in native._command(['/bin/ps', '-axo', 'pid=,uid=']).splitlines():
        match = re.fullmatch(r'\s*([1-9][0-9]*)\s+([0-9]+)\s*', line)
        if match is None or int(match[1]) in seen:
            raise ValueError('native-source-retirement-operations-census-unavailable')
        seen.add(int(match[1]))
        if int(match[2]) == identity['id']:
            raise ValueError('native-source-retirement-operations-process-active')
    if not seen: raise ValueError('native-source-retirement-operations-census-unavailable')


def _source_core_image(path, *, opaque=False, authority=True):
    """No follow, copy or credential-body read for preserved Core data roots."""
    import pixel_macos_custody as custody
    path = Path(path)
    with _source_core_parent(path.parent) as parent:
        observed = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISREG(observed.st_mode): kind = 'file'
        elif stat.S_ISDIR(observed.st_mode): kind = 'directory'
        elif stat.S_ISLNK(observed.st_mode) and opaque: kind = 'symlink'
        else: raise ValueError('native-source-retirement-core-image-invalid')
        fd = None if kind == 'symlink' else os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent)
        try:
            info = observed if fd is None else os.fstat(fd)
            if (info.st_dev, info.st_ino) != (observed.st_dev, observed.st_ino):
                raise ValueError('native-source-retirement-core-image-changed')
            result = dict(inode=[info.st_dev, info.st_ino], uid=info.st_uid, gid=info.st_gid,
                mode=stat.S_IMODE(info.st_mode), kind=kind, sha256=None)
            # Only protected Core authority files are read. Owner/Operations
            # data and every directory/symlink retain their inode, unread.
            if kind == 'file' and not opaque and authority:
                result['sha256'] = hashlib.sha256(custody.protected_bytes(path, limit=32 * 1024 * 1024)).hexdigest()
            after = os.stat(path.name, dir_fd=parent, follow_symlinks=False) if fd is None else os.fstat(fd)
            if (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode, info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (
                    after.st_dev, after.st_ino, after.st_uid, after.st_gid, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError('native-source-retirement-core-image-changed')
            named = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != (info.st_dev, info.st_ino):
                raise ValueError('native-source-retirement-core-image-changed')
            return result
        finally:
            if fd is not None: os.close(fd)


def _source_core_plan(value, *, transaction, witness, cursor, archive):
    from pixel_connections import custody as source
    from pixel_connections import native_retirement as native
    fields = {'schemaVersion', 'kind', 'transaction', 'archiveId', 'archiveInode', 'witness', 'cursor', 'lock',
        'source', 'plan', 'planSha256', 'next'}
    if (type(value) is not dict or set(value) != fields or type(value['schemaVersion']) is not int
            or value['schemaVersion'] != 1 or value['kind'] != 'ods-native-source-core-archive'
            or value['transaction'] != transaction or value['archiveId'] != archive.name
            or value['archiveInode'] != cursor['archiveInode'] or value['witness'] != {
                'inode': cursor['witnessInode'], 'sha256': transaction['transaction']}
            or type(value['cursor']) is not dict or set(value['cursor']) != {'inode', 'sha256'}
            or not source._native_inode(value['cursor']['inode']) or not re.fullmatch('[a-f0-9]{64}', value['cursor']['sha256'])
            or type(value['lock']) is not dict or set(value['lock']) != {'inode', 'uid', 'gid', 'mode', 'kind', 'sha256'}
            or not source._native_inode(value['lock']['inode']) or value['lock'].get('kind') != 'file'
            or value['lock'].get('uid') != 0 or value['lock'].get('mode') != 0o600
            or type(value['lock']['gid']) is not int or value['lock']['gid'] != 0
            or type(value['lock']['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', value['lock']['sha256'])
            or value['source'] != dict(transaction=transaction, inventorySha256=cursor['inventorySha256'],
                planSha256=cursor['planSha256'], phase='core')
            or type(value['plan']) is not list or not 1 <= len(value['plan']) <= 1024
            or value['planSha256'] != hashlib.sha256(source._native_encoded(value['plan'])).hexdigest()
            or type(value['next']) is not int or not 0 <= value['next'] <= len(value['plan'])):
        raise ValueError('native-source-retirement-core-plan-invalid')
    seen = set()
    for row in value['plan']:
        if (type(row) is not dict or set(row) != {'source', 'image'} or type(row['source']) is not str
                or not _source_core_allowed(row['source'], witness) or row['source'] in seen
                or type(row['image']) is not dict or set(row['image']) != {'inode', 'uid', 'gid', 'mode', 'kind', 'sha256'}
                or not source._native_inode(row['image']['inode']) or row['image']['kind'] not in ('file', 'directory', 'symlink')
                or any(type(row['image'][key]) is not int or row['image'][key] < 0 for key in ('uid', 'gid', 'mode'))
                or row['image']['sha256'] is not None and (type(row['image']['sha256']) is not str
                    or not re.fullmatch('[a-f0-9]{64}', row['image']['sha256']))):
            raise ValueError('native-source-retirement-core-plan-invalid')
        seen.add(row['source'])
    if not set(witness['hashes']) <= seen | {str(STATE / name) for name in RETAIN | {'lock'}}:
        raise ValueError('native-source-retirement-core-plan-incomplete')
    for row in value['plan']:
        if row['source'] in witness['hashes'] and row['image']['sha256'] != witness['hashes'][row['source']]:
            raise ValueError('native-source-retirement-core-binding-changed')
    native._bind_plan(witness, cursor)
    return value


def _source_core_receipt(raw, *, value, witness, archive):
    from pixel_connections import custody as source
    receipt = json.loads(raw, object_pairs_hook=source._native_pairs)
    if (type(receipt) is not dict or set(receipt) != {'schema', 'kind', 'owner', 'installDir', 'status', 'moves', 'source'}
            or type(receipt['schema']) is not int or receipt['schema'] != 2
            or receipt['kind'] != 'ods-native-source-retirement-archive' or receipt['owner'] != witness['owner']
            or receipt['installDir'] != witness['source']['owner']['installDir'] or receipt['source'] != value['source']
            or receipt['status'] not in ('retiring', 'retired') or type(receipt['moves']) is not list):
        raise ValueError('native-source-retirement-core-receipt-invalid')
    wanted = [dict(source=row['source'], archive=str(archive / ('item-' + str(index))), done=index < value['next'])
        for index, row in enumerate(value['plan'])]
    # A control reply can be lost after the Core plan CAS but before mirroring
    # its completion into receipt.json. No new move or target is allocated.
    if receipt['moves'] not in (wanted, [] if value['next'] == 0 else [dict(row, done=index < value['next'] - 1)
            for index, row in enumerate(wanted)]):
        raise ValueError('native-source-retirement-core-receipt-changed')
    if receipt['status'] == 'retired' and value['next'] != len(value['plan']):
        raise ValueError('native-source-retirement-core-receipt-changed')
    return receipt, wanted


def _source_core_archive(archive, *, transaction, witness, paths=None):
    """Fixed Source-complete/Core-prefix retry; no external paths or callbacks.

    Only the normal uninstall caller supplies the initial existing fixed Core
    family. Retries reopen the same protected cursor/plan and never recapture
    a changed deployment or select a second archive.
    """
    import pixel_macos_custody as custody
    from pixel_connections import custody as source, native_retirement as native
    archive = Path(archive)
    with custody.protected_directory(STATE) as metadata, custody.protected_directory(archive) as target:
        cursor_raw, cursor_inode = source._native_root_file(metadata, native.CURSOR, limit=32 * 1024 * 1024)
        cursor = native._cursor_value(json.loads(cursor_raw, object_pairs_hook=source._native_pairs), transaction)
        if cursor['phase'] != 'core' or archive != native.ARCHIVE_ROOT / cursor['archiveId']:
            raise ValueError('native-source-retirement-core-prefix-required')
        info = os.fstat(target)
        if [info.st_dev, info.st_ino] != cursor['archiveInode'] or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('native-source-retirement-core-archive-changed')
        try: raw = source._native_root_file(target, native.CORE_PLAN, limit=32 * 1024 * 1024)[0]
        except FileNotFoundError: raw = None
        if raw is None:
            if paths is None: raise ValueError('native-source-retirement-core-plan-required')
            if len(paths) != len(set(map(str, paths))): raise ValueError('native-source-retirement-core-plan-duplicate')
            for path in paths:
                if not _source_core_allowed(path, witness): raise ValueError('native-source-retirement-core-path-refused')
            value = dict(schemaVersion=1, kind='ods-native-source-core-archive', transaction=transaction,
                archiveId=archive.name, archiveInode=cursor['archiveInode'],
                witness=dict(inode=cursor['witnessInode'], sha256=transaction['transaction']),
                cursor=dict(inode=cursor_inode, sha256=hashlib.sha256(cursor_raw).hexdigest()),
                lock=_source_core_image(STATE / 'lock'),
                source=dict(transaction=transaction, inventorySha256=cursor['inventorySha256'], planSha256=cursor['planSha256'], phase='core'),
                plan=[dict(source=str(path), image=_source_core_image(path, opaque=Path(path).parent == BROKER,
                    authority=str(path) in witness['hashes'])) for path in paths], next=0)
            value['planSha256'] = hashlib.sha256(source._native_encoded(value['plan'])).hexdigest()
            _source_core_plan(value, transaction=transaction, witness=witness, cursor=cursor, archive=archive)
            native._core_no_actors(witness); native._identity(witness)
            raw = native._save_at(target, native.CORE_PLAN, value, expected=None)
        else:
            value = _source_core_plan(json.loads(raw, object_pairs_hook=source._native_pairs),
                transaction=transaction, witness=witness, cursor=cursor, archive=archive)
        if value['cursor'] != dict(inode=cursor_inode, sha256=hashlib.sha256(cursor_raw).hexdigest()):
            raise ValueError('native-source-retirement-core-cursor-changed')

        def guard():
            native._core_no_actors(witness); native._identity(witness)
            _source_operations_absent(witness)
            try: witness_raw, witness_inode = source._native_root_file(metadata, 'retirement.json', limit=32 * 1024 * 1024)
            except FileNotFoundError:
                witness_raw, witness_inode = source._native_root_file(target, 'source-witness.json', limit=32 * 1024 * 1024)
            if (witness_inode != cursor['witnessInode'] or hashlib.sha256(witness_raw).hexdigest() != transaction['transaction']
                    or json.loads(witness_raw, object_pairs_hook=source._native_pairs) != witness):
                raise ValueError('native-source-retirement-core-witness-changed')
            if source._native_root_file(target, native.CORE_PLAN, limit=32 * 1024 * 1024)[0] != raw:
                raise ValueError('native-source-retirement-core-plan-changed')
            if source._native_root_file(metadata, native.CURSOR, limit=32 * 1024 * 1024) != (cursor_raw, cursor_inode):
                raise ValueError('native-source-retirement-core-cursor-changed')
            lock_path = STATE / 'lock' if os.path.lexists(STATE / 'lock') else archive / 'source-controller-lock'
            if _source_core_image(lock_path) != value['lock']:
                raise ValueError('native-source-retirement-core-lock-changed')
            for name in RETAIN:
                path = STATE / name
                if str(path) in witness['hashes'] and hashlib.sha256(custody.protected_bytes(path)).hexdigest() != witness['hashes'][str(path)]:
                    raise ValueError('native-source-retirement-core-retained-changed')
            private = witness['source']['inventory']
            kept_source = {native.CURSOR}
            if 'source-identity.json' in private['metadata']:
                kept_source.add('source-identity.json')
                body, inode = source._native_root_file(metadata, 'source-identity.json')
                if dict(inode=inode, sha256=hashlib.sha256(body).hexdigest()) != private['metadata']['source-identity.json']:
                    raise ValueError('native-source-retirement-core-identity-changed')
            names = set(os.listdir(metadata))
            actual_source = {name for name in names if name.startswith(native._SOURCE_PREFIXES) or name == 'native-app-enrollment.json'}
            if actual_source != kept_source: raise ValueError('native-source-retirement-core-new-source-state')
            validate_state_names(names - kept_source)
            expected_state = kept_source | {name for name in RETAIN if str(STATE / name) in witness['hashes']}
            expected_state.update(name for name in ('lock', 'retirement.json') if source._native_at_inode(metadata, name) is not None)
            expected_state.update(Path(row['source']).name for row in value['plan']
                if Path(row['source']).parent == STATE and os.path.lexists(row['source']))
            if names != expected_state:
                raise ValueError('native-source-retirement-core-new-state')
            publisher = helper('pixel-native-source-broker')
            for index, row in enumerate(cursor['plan']):
                native._verify_entry(target, native._destination(index), row, witness, publisher)
            # Verify every exact live or archived inode, including a rename
            # whose acknowledgement was lost at the current index.
            for index, row in enumerate(value['plan']):
                origin, saved = Path(row['source']), archive / ('item-' + str(index))
                live, done = os.path.lexists(origin), os.path.lexists(saved)
                if live and not done and index >= value['next']:
                    actual = _source_core_image(origin, opaque=origin.parent == BROKER, authority=row['image']['sha256'] is not None)
                elif done and not live and index <= value['next']:
                    actual = _source_core_image(saved, opaque=origin.parent == BROKER, authority=row['image']['sha256'] is not None)
                else: raise ValueError('native-source-retirement-core-image-changed')
                if actual != row['image']: raise ValueError('native-source-retirement-core-image-changed')
            receipt_raw = source._native_root_file(target, 'receipt.json')[0]
            receipt, wanted = _source_core_receipt(receipt_raw, value=value, witness=witness, archive=archive)
            expected_names = {'receipt.json', native.CORE_PLAN, *[native._destination(i) for i in range(len(cursor['plan']))]}
            expected_names.update('item-' + str(i) for i in range(len(value['plan'])) if os.path.lexists(archive / ('item-' + str(i))))
            expected_names.update(name for name in ('source-witness.json', 'source-controller-lock') if os.path.lexists(archive / name))
            if set(os.listdir(target)) != expected_names:
                raise ValueError('native-source-retirement-core-archive-unbound')
            return receipt_raw, receipt, wanted

        receipt_raw, receipt, wanted = guard()
        if receipt['moves'] != wanted:
            native._save_at(target, 'receipt.json', dict(receipt, moves=wanted), expected=receipt_raw)
        while value['next'] < len(value['plan']):
            guard()
            index = value['next']
            origin, saved = Path(value['plan'][index]['source']), archive / ('item-' + str(index))
            if os.path.lexists(origin):
                with _source_core_parent(origin.parent) as parent:
                    if _source_core_image(origin, opaque=origin.parent == BROKER,
                            authority=value['plan'][index]['image']['sha256'] is not None) != value['plan'][index]['image']:
                        raise ValueError('native-source-retirement-core-image-changed')
                    os.rename(origin.name, saved.name, src_dir_fd=parent, dst_dir_fd=target)
                    os.fsync(parent); os.fsync(target)
            value['next'] += 1
            raw = native._save_at(target, native.CORE_PLAN, value, expected=raw)
            receipt_raw, receipt, wanted = guard()
            native._save_at(target, 'receipt.json', dict(receipt, moves=wanted), expected=receipt_raw)
        receipt_raw, receipt, wanted = guard()
        native._save_at(target, 'receipt.json', dict(receipt, moves=wanted, status='retired'), expected=receipt_raw)
        return {'status': 'retired', 'archive': str(archive)}


def _source_finish_controls(archive, *, transaction, witness):
    """Archive the exact last witness/lock/cursor, with rename-ACK recovery."""
    import pixel_macos_custody as custody
    from pixel_connections import custody as source, native_retirement as native
    archive = Path(archive)
    # Full Core and Source prefix proof immediately precedes final controls.
    result = _source_core_archive(archive, transaction=transaction, witness=witness)
    with custody.protected_directory(STATE) as metadata, custody.protected_directory(archive) as target:
        cursor_raw, cursor_inode = source._native_root_file(metadata, native.CURSOR, limit=32 * 1024 * 1024)
        cursor = native._cursor_value(json.loads(cursor_raw, object_pairs_hook=source._native_pairs), transaction)
        plan_raw = source._native_root_file(target, native.CORE_PLAN, limit=32 * 1024 * 1024)[0]
        plan = _source_core_plan(json.loads(plan_raw, object_pairs_hook=source._native_pairs),
            transaction=transaction, witness=witness, cursor=cursor, archive=archive)
        expected = (
            ('retirement.json', 'source-witness.json', plan['witness']),
            ('lock', 'source-controller-lock', dict(inode=plan['lock']['inode'], sha256=plan['lock']['sha256'])),
            (native.CURSOR, 'source-cursor.json', dict(inode=cursor_inode, sha256=hashlib.sha256(cursor_raw).hexdigest())),
        )
        for current, saved, image in expected:
            native._core_no_actors(witness); native._identity(witness)
            _source_operations_absent(witness)
            first, second = source._native_at_inode(metadata, current), source._native_at_inode(target, saved)
            if first == image['inode'] and second is None:
                raw, inode = source._native_root_file(metadata, current, limit=32 * 1024 * 1024)
                if dict(inode=inode, sha256=hashlib.sha256(raw).hexdigest()) != image:
                    raise ValueError('native-source-retirement-final-control-changed')
                os.rename(current, saved, src_dir_fd=metadata, dst_dir_fd=target)
                os.fsync(metadata); os.fsync(target)
            elif first is not None or second != image['inode']:
                raise ValueError('native-source-retirement-final-control-changed')
            raw, inode = source._native_root_file(target, saved, limit=32 * 1024 * 1024)
            if dict(inode=inode, sha256=hashlib.sha256(raw).hexdigest()) != image:
                raise ValueError('native-source-retirement-final-control-changed')
        expected_names = RETAIN | ({'source-identity.json'} if 'source-identity.json' in witness['source']['inventory']['metadata'] else set())
        if set(os.listdir(metadata)) != expected_names:
            raise ValueError('native-source-retirement-final-state-changed')
    return result


def _resume_source_core_retirement(owner, root):
    """Resume only the already-bound Source-complete Core move transaction."""
    import pixel_macos_custody as custody
    from pixel_connections import custody as source, native_retirement as native
    with custody.protected_directory(STATE) as metadata:
        cursor_raw = source._native_root_file(metadata, native.CURSOR, limit=32 * 1024 * 1024)[0]
        cursor = json.loads(cursor_raw, object_pairs_hook=source._native_pairs)
        transaction = native._transaction(cursor['transaction'])
        native._cursor_value(cursor, transaction)
        if cursor['phase'] != 'core': raise ValueError('native-source-retirement-core-prefix-required')
        archive = native.ARCHIVE_ROOT / cursor['archiveId']
        with custody.protected_directory(archive) as target:
            try: raw = source._native_root_file(metadata, 'retirement.json', limit=32 * 1024 * 1024)[0]
            except FileNotFoundError: raw = source._native_root_file(target, 'source-witness.json', limit=32 * 1024 * 1024)[0]
            if source_retirement_transaction(raw) != transaction:
                raise ValueError('native-source-retirement-core-witness-changed')
            witness = json.loads(raw, object_pairs_hook=source._native_pairs)
            if witness['owner'] != owner.pw_uid or witness['source']['owner'] != dict(
                    uid=owner.pw_uid, gid=owner.pw_gid, name=owner.pw_name, installDir=str(root)):
                raise ValueError('native-source-retirement-core-owner-changed')
            plan = _source_core_plan(json.loads(source._native_root_file(target, native.CORE_PLAN,
                limit=32 * 1024 * 1024)[0], object_pairs_hook=source._native_pairs),
                transaction=transaction, witness=witness, cursor=cursor, archive=archive)
            directory, filename = (metadata, 'lock') if source._native_at_inode(metadata, 'lock') is not None else (target, 'source-controller-lock')
            lock = os.open(filename, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
            try:
                info = custody._verify_fd(lock, directory=False)
                if [info.st_dev, info.st_ino] != plan['lock']['inode']:
                    raise ValueError('native-source-retirement-core-lock-changed')
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = _source_finish_controls(archive, transaction=transaction, witness=witness)
            finally: os.close(lock)
    account = helper('pixel-native-ops-account')
    options = {'source_owner': owner.pw_name, 'source_install_dir': str(root)} if os.path.lexists(STATE / 'source-identity.json') else {}
    account.verify_empty_home_only(**options) if os.path.lexists(BROKER) else account.verify_identity_only(**options)
    return result


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
    from pixel_connections import custody as source_custody, native_retirement as native_source_retirement
    if os.path.lexists(STATE / native_source_retirement.CURSOR):
        # Once a fixed Core plan exists, directories/settings may already have
        # moved. Reopen that exact transaction before any new plan or archive.
        with custody.protected_directory(STATE) as metadata:
            raw = source_custody._native_root_file(metadata, native_source_retirement.CURSOR,
                limit=32 * 1024 * 1024)[0]
        cursor = json.loads(raw, object_pairs_hook=source_custody._native_pairs)
        source_custody._native_root()
        native_source_retirement._cursor_value(cursor, native_source_retirement._transaction(cursor['transaction']))
        archived = native_source_retirement.ARCHIVE_ROOT / cursor['archiveId']
        if cursor['phase'] == 'core' and os.path.lexists(archived / native_source_retirement.CORE_PLAN):
            if validate_only or prepare_stopped_recovery or resume_stopped_recovery:
                raise ValueError('native-source-retirement-use-existing-core-recovery')
            return _resume_source_core_retirement(owner, root)
    residues = [Path(p) for p in (*DIRECTORIES, *INTEGRATION_DIRECTORIES)] + [BROKER, STATE]
    source_residues = [Path(p) for p in SOURCE_RESIDUES]
    residues += [Path('/private/etc/ods') / n for n in CONFIG_NAMES]
    plists = [Path('/Library/LaunchDaemons') / ('com.ods.pixel-' + n + '.plist')
              for n in (*LABELS, INTEGRATION_LABEL)]
    if not os.path.lexists(SETTINGS):
        if any(os.path.lexists(p) for p in residues + plists + source_residues):
            # A previous successful retirement may retain only the identity.
            others = [p for p in residues + plists + source_residues if p not in (STATE, BROKER)]
            if any(os.path.lexists(p) for p in others):
                raise ValueError('native-retirement-unbound-residue')
            account = helper('pixel-native-ops-account')
            source = os.path.lexists(STATE / 'source-identity.json')
            options = {'source_owner': owner_name, 'source_install_dir': str(root)} if source else {}
            (account.verify_empty_home_only(**options) if os.path.lexists(BROKER)
                else account.verify_identity_only(**options))
        return {'status': 'absent'}
    source_metadata = any(p.name.startswith(native_source_retirement._SOURCE_PREFIXES) for p in STATE.iterdir())
    descriptor_only = (len(source_residues) >= 2
        and set(source_residues[-2:]) == {source_custody.NATIVE_RUNTIME_DESCRIPTOR, source_custody.NATIVE_RUNTIME_MANIFEST}
        and all(os.path.lexists(p) for p in source_residues[-2:])
        and not source_metadata and not any(os.path.lexists(p) for p in source_residues[:-2]))
    source_mode = any(os.path.lexists(p) for p in source_residues) or source_metadata
    if source_mode:
        # An old protected bridge without the typed capability is never
        # silently adopted. Lexical existence here grants no authority: the
        # actual old sealed selection is independently required below.
        if not descriptor_only and (not os.path.lexists(source_custody.NATIVE_PROGRAM / 'pixel_connections/native_retirement.py')
                or not os.path.lexists(source_custody.NATIVE_RUNTIME_DESCRIPTOR)
                or not os.path.lexists(source_custody.NATIVE_RUNTIME_MANIFEST)):
            raise ValueError('native-source-retirement-not-qualified')
        if prepare_stopped_recovery or resume_stopped_recovery:
            raise ValueError('native-source-retirement-core-reboot-path-not-qualified')
    with custody.protected_directory(STATE) as directory:
        lock = os.open('lock', os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory)
        try:
            custody._verify_fd(lock, directory=False)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            source_names = {name for name in os.listdir(directory) if name.startswith(native_source_retirement._SOURCE_PREFIXES)
                or name == 'native-app-enrollment.json'} if source_mode else set()
            # This filtering only keeps Source out of the legacy reader. The
            # typed inventory must independently account for every Source name
            # before a witness, stop or archive is admitted.
            validate_state_names(set(os.listdir(directory)) - source_names)
            def record(path):
                return json.loads(custody.protected_bytes(path, limit=32 * 1024 * 1024))
            settings = record(SETTINGS)
            installation = record(STATE / 'installation.json')
            services = record(STATE / 'service-installation.json')
            selected(settings, installation, services, owner=owner, install_dir=root)
            legacy_descriptors = legacy_source_bridge = None
            if descriptor_only:
                legacy_source_bridge = _source_retirement_bridge(settings, owner)
                with legacy_source_bridge.bounded(900):
                    from pixel_access_bridge import LaunchdAccessBridge
                    LaunchdAccessBridge.discover(legacy_source_bridge)
                    legacy_descriptors = _legacy_source_descriptor_proof(legacy_source_bridge)
                source_mode = False
            roles = set(services.get('recovery', {}).get('definitions', {}))
            legacy = {'manager', 'promoter', 'operations'}
            if roles not in (legacy, legacy | {'integrations'}):
                raise ValueError('native-retirement-service-set-incomplete')
            has_integrations = 'integrations' in roles
            verify_integrations = lambda: None
            retained = None
            if not has_integrations:
                preservation = helper('pixel-native-integrations-preservation')
                retained = preservation.load(STATE, custody)
                if (any(os.path.lexists(Path(p)) for p in helper('pixel-runtime-upgrade').INTEGRATION_ADDITIONS)
                        or not prove_absent('system/com.ods.pixel-native-integrations')):
                    raise ValueError('native-retirement-unbound-integrations')
                if retained is not None:
                    body = preservation.validate(retained, owner=owner, install_dir=root,
                        settings=custody.protected_bytes(SETTINGS),
                        services=custody.protected_bytes(STATE / 'service-installation.json'),
                        installation=custody.protected_bytes(STATE / 'installation.json'))
                    if integration_disabled():
                        raise ValueError('native-retirement-unbound-integrations')
                    verify_integrations = lambda: helper('pixel-native-integrations-service').validate_preserved(
                        owner_name, common_body=body, allow_partial=True)
                    verify_integrations()
                elif any(os.path.lexists(Path(p)) for p in INTEGRATION_DIRECTORIES):
                    raise ValueError('native-retirement-unbound-integrations')
                plists = plists[:-1]
                if retained is None:
                    residues = [p for p in residues if str(p) not in INTEGRATION_DIRECTORIES]
            else:
                verify_integrations = lambda: helper('pixel-native-integrations-service').validate_retirement(owner_name)
                verify_integrations()
            reboot = helper('pixel-native-retirement-reboot').Recovery(STATE, plists,
                read=lambda path: custody.protected_bytes(path, limit=32 * 1024 * 1024),
                directory=custody.protected_directory, save=atomic_json,
                command=command, absent=prove_absent)
            if resume_stopped_recovery and reboot.document is None:
                raise ValueError('native-retirement-reboot-preparation-required')
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
            snapshots = {str(p): custody.protected_bytes(reboot.path(p), limit=32 * 1024 * 1024)
                         for p in [SETTINGS, *plists, *([Path(p) for p in SOURCE_RESIDUES[-2:]] if legacy_descriptors is not None else []),
                             *(STATE / n for n in os.listdir(directory)
                             if n not in ('retirement.json', 'retirement-plists') and n not in source_names)]}
            gateway = plistlib.loads(snapshots[str(plists[0])])
            if launchd_definition_digest(gateway, ValueError) != settings.get('gateway_binding', {}).get('definition'):
                raise ValueError('native-retirement-gateway-binding-mismatch')
            sandboxes = NativeSandboxes(gateway, owner=owner, root=root)
            sandbox_plans = sandboxes.plan()
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
                if role in ('native-manager', 'native-promoter', 'native-operations', INTEGRATION_LABEL):
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
                if source_mode:
                    bridge = _source_retirement_bridge(settings, owner)
                    with bridge.bounded(900):
                        from pixel_access_bridge import LaunchdAccessBridge
                        LaunchdAccessBridge.discover(bridge)
                        from pixel_connections.source_backend import for_bridge
                        backend = for_bridge(bridge, linux_paths=source_custody.Paths(), linux_run=source_custody.system_run)
                        selection = backend.selection
                        if os.path.lexists(source_custody.NATIVE_STATE):
                            native_source_retirement.add_history(selection, source_custody.native_retirement_inventory(selection))
                        else:
                            native_source_retirement.code_only_inventory(selection)
                return {'status': 'validated', 'owner': owner.pw_uid, 'installDir': str(root)}
            reboot.unchanged(snapshots)
            source_prepared = None
            if source_mode:
                core = dict(schema=1, owner=owner.pw_uid, boot=boot, hashes=hashes, trees=trees, sandboxes=sandbox_plans)
                if os.path.lexists(witness_path) and all(not loaded for _, _, loaded in jobs):
                    raw = custody.protected_bytes(witness_path, limit=32 * 1024 * 1024)
                    transaction = source_retirement_transaction(raw)
                    source_prepared = {'transaction': transaction, 'witness': json.loads(raw)}
                else:
                    bridge = _source_retirement_bridge(settings, owner)
                    with bridge.bounded(900): source_prepared = _prepare_source_retirement(bridge, owner, core)
            elif not os.path.lexists(witness_path):
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
            # The legacy gateway has now stopped too. It cannot alter these
            # owner-writable private runtime/log paths between checks.
            verify_integrations()
            if source_mode:
                # The final retained-identity check would also refuse a live
                # Operations UID; prove it before moving Source/Core data.
                _source_operations_absent(source_prepared['witness'])
            if legacy_descriptors is not None:
                with legacy_source_bridge.bounded(900):
                    _verify_legacy_source_descriptor_proof(legacy_source_bridge, legacy_descriptors)
            if source_mode:
                cursor_path = STATE / native_source_retirement.CURSOR
                if os.path.lexists(cursor_path):
                    current_cursor = json.loads(custody.protected_bytes(cursor_path, limit=32 * 1024 * 1024))
                    native_source_retirement._cursor_value(current_cursor, source_prepared['transaction'])
                    archive = native_source_retirement.ARCHIVE_ROOT / current_cursor['archiveId']
                else:
                    archive = native_source_retirement.ARCHIVE_ROOT / source_prepared['witness']['source']['nonce']
            else:
                archive = Path('/private/var/lib/ods-pixel-retired') / uuid.uuid4().hex
            if source_mode:
                with custody.protected_directory(native_source_retirement.ARCHIVE_ROOT, create=True) as parent:
                    if not os.path.lexists(archive):
                        os.mkdir(archive.name, mode=0o700, dir_fd=parent)
                        os.fsync(parent)
                    with custody.protected_directory(archive) as target:
                        if stat.S_IMODE(os.fstat(target).st_mode) != 0o700:
                            raise ValueError('native-source-retirement-archive-custody-changed')
            else:
                with custody.protected_directory(archive, create=True): pass
                os.chmod(archive, 0o700)
            if source_mode:
                # No Source path is added to the legacy move loop. The typed
                # cursor finishes first, preserving all old data/authority.
                if not os.path.lexists(archive / 'receipt.json'):
                    source = dict(transaction=source_prepared['transaction'],
                        inventorySha256=hashlib.sha256(source_custody._native_encoded(source_prepared['witness']['source']['inventory'])).hexdigest(),
                        planSha256=None, phase='prepared')
                    with custody.protected_directory(archive) as target:
                        native_source_retirement._save_at(target, 'receipt.json', dict(schema=2,
                            kind=native_source_retirement.ARCHIVE_KIND, owner=owner.pw_uid, installDir=str(root),
                            status='retiring', moves=[], source=source), expected=None)
                progress = source_custody.native_retirement_archive_begin(transaction=source_prepared['transaction'], archive_id=archive.name)
                while progress['phase'] != 'core':
                    progress = source_custody.native_retirement_archive_step(transaction=source_prepared['transaction'])
            moves = []
            # Preserve the newly declared private family first. An interruption
            # anywhere within this prefix leaves all legacy deployment authority
            # intact, so the next retirement can validate remaining fixed roots
            # and keep the earlier interrupted archive for diagnosis.
            private_moves = [Path(p) for p in INTEGRATION_DIRECTORIES] if retained is not None else []
            moves.extend(p for p in private_moves if os.path.lexists(p))
            # The Operations account/home may be protected by macOS. Retain the
            # empty home and UUID-bound identity; preserve each former child.
            if os.path.lexists(BROKER):
                info = BROKER.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != intent['id'] or info.st_gid != intent['id']:
                    raise ValueError('native-retirement-broker-home-custody')
                moves.extend(BROKER.iterdir())
            moves += [p for p in residues + plists if p not in (STATE, BROKER, *private_moves) and os.path.lexists(p)]
            keep = RETAIN | ({'source-identity.json', native_source_retirement.CURSOR, 'retirement.json', 'lock'} if source_mode else set())
            moves += [STATE / n for n in os.listdir(directory) if n not in keep]
            if legacy_descriptors is not None:
                moves.extend(Path(p) for p in SOURCE_RESIDUES[-2:])
            if source_mode:
                moves.extend(Path(p) for p in SOURCE_RESIDUES[-2:] if os.path.lexists(p))
                result = _source_core_archive(archive, transaction=source_prepared['transaction'],
                    witness=source_prepared['witness'], paths=moves)
                result = _source_finish_controls(archive, transaction=source_prepared['transaction'], witness=source_prepared['witness'])
                options = dict(source_owner=owner.pw_name, source_install_dir=str(root)) if os.path.lexists(STATE / 'source-identity.json') else {}
                account.verify_empty_home_only(**options) if os.path.lexists(BROKER) else account.verify_identity_only(**options)
                # Source-bearing archives and stopped sandboxes are retained.
                return result
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
        if str(error) == 'native-retirement-stopped-job-needs-witness':
            print('Keep native state intact. Maintainer-assisted --prepare-stopped-recovery requires a Mac restart; '
                  'see docs/MACOS_PIXEL_RETIREMENT_RECOVERY.md before using it.', file=sys.stderr)
        return 1


def _protected_cli(argv):
    """Capture fixed code only; retain the caller's owner/data arguments."""
    if sys.platform != 'darwin' or os.geteuid() != 0 or any(flag in argv for flag in ('--help', '-h')):
        return None
    sys.path.insert(0, str(HERE.parents[2] / 'bin'))
    import pixel_native_installer_entry as entry
    active = entry.active_support()
    if active is not None:
        if active['peer'] != 'native-uninstall' or Path(active['root']) != HERE.parents[2]:
            raise ValueError('native-installer-support-peer-invalid')
        return None
    python = helper('pixel-native-services').helper('ops-service').select_python()
    support = entry.stage_support(HERE.parents[2], python=python)
    if (type(support) is not dict or set(support) != {'root', 'entry', 'codeSha256', 'python'}
            or Path(support['root']) != entry.SUPPORT_ROOT / support['codeSha256']
            or Path(support['entry']) != Path(support['root']) / entry.ENTRY):
        raise ValueError('native-installer-support-publication-invalid')
    return subprocess.run([support['python'], '-I', '-B', support['entry'], '--peer', 'native-uninstall',
        '--', *argv], check=False, timeout=1800).returncode


if __name__ == '__main__':
    try:
        protected_result = _protected_cli(sys.argv[1:])
        raise SystemExit(main() if protected_result is None else protected_result)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print('error: native-installer-support-custody-failed', file=sys.stderr)
        raise SystemExit(1)
