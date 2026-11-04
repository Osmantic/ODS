"""CPU Source custody controls with real FDs and explicit Darwin metadata simulation.

Actual Root deployment/runtime/bundle requalification is retained via the existing
path fixture. Only platform, account database, Root ownership and Apple ACL calls
are simulated. No installed files, accounts, ACLs, jobs, providers or models.
"""
# ruff: noqa: E402 -- POSIX-only dependency fixture imports follow platform skip.
from contextlib import contextmanager
import copy
import ctypes
import errno
import hashlib
import json
import os
import plistlib
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
import uuid

import pytest

if sys.platform == 'win32':
    pytest.skip('POSIX descriptor fixture', allow_module_level=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'bin'), str(ROOT / 'tests')]
from pixel_connections import custody as source
import pixel_macos_custody as mac
from test_connection_deployment_paths import deployment  # noqa: F401 -- real shared fixture


class AppleACL:
    """Documented ACL API behavior only; not a Darwin ABI/kernel claim."""
    def __init__(self):
        self.objects, self.acls, self.buffers, self.identities = {}, {}, [], {}
        self.group_identities, self.memberships, self.membership_queries = {}, {}, []
        self.membership_error = 0
        self.next = 100
        self.writes = 0
        self.fail_write = False

    def key(self, fd):
        info = os.fstat(fd)
        return info.st_dev, info.st_ino

    def put(self, item):
        self.next += 1
        self.objects[self.next] = item
        return self.next

    def obj(self, ptr):
        return self.objects[ptr.value if hasattr(ptr, 'value') else ptr]

    def acl_get_fd_np(self, fd, kind):
        assert kind == 0x100
        if self.key(fd) not in self.acls:
            ctypes.set_errno(errno.ENOENT)
            return None
        return self.put(copy.deepcopy(self.acls[self.key(fd)]))

    def acl_valid(self, ptr):
        return 0 if self.obj(ptr).get('valid', True) else -1

    def acl_get_flagset_np(self, ptr, out):
        out._obj.value = ptr.value if hasattr(ptr, 'value') else ptr
        return 0

    def acl_get_flag_np(self, ptr, flag):
        return int(bool(self.obj(ptr).get('flags', 0) & (flag & 0xffffffff)))

    def acl_get_entry(self, ptr, which, out):
        value = self.obj(ptr)
        index = 0 if which == 0 else value.get('index', -1) + 1
        value['index'] = index
        if index >= len(value['entries']):
            ctypes.set_errno(errno.EINVAL)
            return -1
        out._obj.value = self.put(value['entries'][index])
        return 0

    def acl_get_tag_type(self, ptr, out):
        out._obj.value = self.obj(ptr)['tag']
        return 0

    def acl_get_permset_mask_np(self, ptr, out):
        out._obj.value = self.obj(ptr)['mask']
        return 0

    def acl_get_qualifier(self, ptr):
        buf = ctypes.create_string_buffer(self.obj(ptr)['uuid'])
        self.buffers.append(buf)
        return ctypes.addressof(buf)

    def acl_free(self, _ptr):
        return 0

    def mbr_uid_to_uuid(self, uid, out):
        value = self.identities.setdefault(uid, uuid.uuid5(uuid.NAMESPACE_OID, str(uid)).bytes)
        ctypes.memmove(out, value, 16)
        return 0

    def mbr_gid_to_uuid(self, gid, out):
        value = self.group_identities.setdefault(gid, uuid.uuid5(uuid.NAMESPACE_DNS, str(gid)).bytes)
        ctypes.memmove(out, value, 16)
        return 0

    def mbr_check_membership(self, user, group, out):
        pair = bytes(user), bytes(group)
        self.membership_queries.append(pair)
        if self.membership_error:
            return self.membership_error
        out._obj.value = self.memberships.get(pair, 0)
        return 0

    def acl_init(self, count):
        assert count == 2
        return self.put({'entries': [], 'flags': 0})

    def acl_create_entry(self, acl, out):
        entry = {'flags': 0}
        self.obj(acl._obj)['entries'].append(entry)
        out._obj.value = self.put(entry)
        return 0

    def acl_set_tag_type(self, ptr, tag):
        self.obj(ptr)['tag'] = tag
        return 0

    def acl_set_qualifier(self, ptr, qualifier):
        self.obj(ptr)['uuid'] = bytes(qualifier)
        return 0

    def acl_set_permset_mask_np(self, ptr, mask):
        self.obj(ptr)['mask'] = mask
        return 0

    def acl_set_fd_np(self, fd, ptr, kind):
        assert kind == 0x100
        if self.fail_write:
            return -1
        self.writes += 1
        self.acls[self.key(fd)] = copy.deepcopy(self.obj(ptr))
        return 0

    def entry(self, uid, **changes):
        value = {'tag': 1, 'mask': 10, 'flags': 0,
                 'uuid': self.identities.setdefault(uid, uuid.uuid5(uuid.NAMESPACE_OID, str(uid)).bytes)}
        return dict(value, **changes)


@pytest.fixture
def native(deployment, tmp_path, monkeypatch):  # noqa: F811 -- imported pytest fixture
    f = deployment
    world = tmp_path / 'native'
    world.mkdir(mode=0o700)
    metadata, kit = world / 'access', world / 'kit'
    metadata.mkdir(mode=0o700)
    kit.mkdir(mode=0o755)
    for name, value in [('NATIVE_META', metadata), ('NATIVE_KIT', kit),
                        ('NATIVE_STATE', world / 'state'), ('NATIVE_ENV', world / 'state/source-broker.env'),
                        ('NATIVE_LEGACY_ENV', world / 'legacy-source.env')]:
        monkeypatch.setattr(source, name, value)
    # Explicit platform/root metadata simulation; the real guard's exact types,
    # RuntimeCustody.inspect and bundle/plugin/plist requalification still run.
    monkeypatch.setattr(source, '_native_root', lambda: None)
    real_fstat, real_fchown = os.fstat, os.fchown
    owners, changes = {}, []

    def fstat(fd):
        info = real_fstat(fd)
        try:
            path = Path(os.readlink('/proc/self/fd/' + str(fd)))
            inside = path.is_relative_to(world)
        except (OSError, ValueError):
            inside = False
        if not inside:
            return info
        data = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
        data['st_uid'], data['st_gid'] = owners.get((info.st_dev, info.st_ino), (0, 0))
        return SimpleNamespace(**data)

    def chown(fd, uid, gid):
        path = Path(os.readlink('/proc/self/fd/' + str(fd)))
        assert path.is_relative_to(world)
        info = real_fstat(fd)
        owners[(info.st_dev, info.st_ino)] = uid, gid
        changes.append((path.name, uid, gid))

    monkeypatch.setattr(os, 'fstat', fstat)
    monkeypatch.setattr(os, 'fchown', chown)
    @contextmanager
    def protected_directory(path, **kwargs):
        assert not kwargs and Path(path) in (metadata, kit, world)
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield fd
        finally:
            os.close(fd)
    monkeypatch.setattr(mac, 'protected_directory', protected_directory)
    acl = AppleACL()
    monkeypatch.setattr(source, '_native_acl_library', lambda: acl)
    selected = source.native_source_selection(f.bridge, f.value)
    broker = b'print("invented never executed broker")\n'
    env = b"PIXEL_CALENDAR_DIRECT_ENABLED='0'\n"
    for name, body, mode in [('broker.py', broker, 0o755), ('source-broker.env', env, 0o644)]:
        (kit / name).write_bytes(body)
        (kit / name).chmod(mode)
    manifest = {'schemaVersion': 1, 'kind': 'ods-native-source-kit',
        'deploymentSha256': f.value.checksum, 'sourceRef': 'a' * 40,
        'files': {'broker.py': hashlib.sha256(broker).hexdigest(),
                  'source-broker.env': hashlib.sha256(env).hexdigest()}}
    (kit / 'source-kit.json').write_text(json.dumps(manifest))
    (kit / 'source-kit.json').chmod(0o644)
    identity = {'schema': 1, 'name': source.NATIVE_BROKER, 'id': 60001,
                'userGuid': str(uuid.uuid4()).upper(), 'groupGuid': str(uuid.uuid4()).upper()}
    acl.identities[identity['id']] = uuid.UUID(identity['userGuid']).bytes
    acl.group_identities[identity['id']] = uuid.UUID(identity['groupGuid']).bytes
    records = {kind: {k: [v] for k, v in source._native_identity_attributes(identity, kind).items()}
               for kind in ('Groups', 'Users')}
    monkeypatch.setattr(source, '_native_ds_record', lambda kind: copy.deepcopy(records[kind]))
    monkeypatch.setattr(source.pwd, 'getpwall', lambda: [])
    monkeypatch.setattr(source.grp, 'getgrall', lambda: [])
    actual_user, actual_group = source.pwd.getpwnam, source.grp.getgrnam
    def user_lookup(name):
        if name == f.bridge.gateway_owner:
            # Directory Service's owner home is relocated with this confined
            # Darwin namespace. Keep its actual Linux UID/GID/name unchanged.
            current = actual_user(name)
            return SimpleNamespace(pw_uid=current.pw_uid, pw_gid=current.pw_gid, pw_name=current.pw_name,
                pw_dir=getattr(f.bridge.owner, 'pw_dir', str(f.bridge.home)))
        if name != source.NATIVE_BROKER:
            return actual_user(name)
        record = records['Users']
        if record is None or 'UniqueID' not in record:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=int(record['UniqueID'][0]),pw_gid=int(record['PrimaryGroupID'][0]))
    monkeypatch.setattr(source.pwd,'getpwnam',user_lookup)
    def group_lookup(name):
        if name != source.NATIVE_BROKER:
            return actual_group(name)
        record = records['Groups']
        if record is None or 'PrimaryGroupID' not in record:
            raise KeyError(name)
        return SimpleNamespace(gr_name=source.NATIVE_BROKER,gr_gid=int(record['PrimaryGroupID'][0]),gr_mem=[])
    monkeypatch.setattr(source.grp, 'getgrnam', group_lookup)
    (metadata / 'source-identity.json').write_text(json.dumps(identity))
    (metadata / 'source-identity.json').chmod(0o600)
    # The real Root guard resolves the owner through pwd too. Preserve that
    # lookup, only the dedicated broker's lookup is simulated.
    original_pwd = __import__('pwd').getpwuid
    assert original_pwd(os.getuid()).pw_uid == f.value.document['ownerUid']
    return SimpleNamespace(f=f, world=world, metadata=metadata, kit=kit, selected=selected,
        identity=identity, records=records, acl=acl, changes=changes, owners=owners,
        real_fstat=real_fstat, real_fchown=real_fchown)


def test_fixed_paths_refuse_any_owner_relocation():
    assert source.NativeSourcePaths().state == Path('/private/var/lib/pixel-source-broker')
    with pytest.raises(source.CustodyError, match='paths-invalid'):
        source.NativeSourcePaths(state=Path('/tmp/owner-choice'))


@pytest.mark.parametrize('arguments,role,operation', [
    (['systemctl', 'daemon-reload'], 'definitions', 'verify'),
    (['systemctl', 'enable', '--now', 'pixel-source-broker.timer'], 'sync', 'enable-trigger'),
    (['systemctl', 'disable', '--now', 'pixel-source-direct.path'], 'direct', 'disable-trigger'),
    (['systemctl', 'start', '--no-block', 'pixel-source-broker.service'], 'sync', 'observe-or-start'),
    (['systemctl', 'stop', 'pixel-source-action@*.service'], 'action', 'quiesce-family'),
    (['systemctl', 'start', 'pixel-source-exchange@' + 'a'*32 + '.service'], 'exchange', 'observe-or-start-once'),
    (['systemctl', 'start', 'pixel-source-action@calendar-1793650000000-abcdef12.service'], 'action', 'observe-or-start-once'),
    (['systemctl', 'start', 'pixel-source-reconcile@gmail-draft-1793650000000-abcdef12.service'], 'reconcile', 'observe-or-start-once'),
    (['systemctl', 'start', 'pixel-source-revoke.service'], 'revoke', 'observe-or-start-once'),
])
def test_closed_run_mapping_is_data_only(arguments, role, operation):
    result = source.native_source_command(arguments, 30)
    assert result.role == role and result.operation == operation
    assert result.label is None or result.label.startswith('com.ods.pixel-native-source-')


@pytest.mark.parametrize('arguments,timeout', [
    (['launchctl', 'kickstart', '-k', 'system/anything'], 30),
    (['systemctl', 'start', 'arbitrary.service'], 30),
    (['systemctl', 'start', 'pixel-source-action@../../owner.service'], 30),
    (['systemctl', 'start', 'pixel-source-action@calendar-1-abcdef12.service'], 30),
    (['systemctl', 'start', 'pixel-source-exchange@'+'A'*32+'.service'], 30),
    (['systemctl', 'start', 'pixel-source-revoke.service'], 61),
    (['systemctl', 'start', 'pixel-source-exchange@'+'a'*32+'.service'], 121),
    (['systemctl', 'daemon-reload'], True),
    (('systemctl', 'daemon-reload'), 30),
])
def test_closed_mapping_refuses_widening(arguments, timeout):
    with pytest.raises(source.CustodyError, match='command-invalid'):
        source.native_source_command(arguments, timeout)


def test_parse_or_duck_guard_alone_never_authorizes_source_writes(native):
    fake = SimpleNamespace(guard=lambda: native.f.value.document, deployment=native.f.value)
    with pytest.raises(source.CustodyError, match='deployment-required'):
        source.native_prepare_identity(fake)
    with pytest.raises(source.CustodyError, match='deployment-required'):
        source.native_source_selection(object(), native.f.value)
    assert not source.NATIVE_STATE.exists() and not native.changes


def test_real_guard_requalifies_runtime_before_identity_or_state(native):
    (native.f.root / 'runtime/openclaw.mjs').write_bytes(b'changed')
    before = (native.metadata / 'source-identity.json').read_bytes()
    with pytest.raises(Exception, match='custody-unqualified'):
        source.native_prepare_state(native.selected, native.identity)
    assert (native.metadata / 'source-identity.json').read_bytes() == before
    assert not source.NATIVE_STATE.exists() and not native.changes


@pytest.mark.parametrize('mutation', ['body', 'manifest-deployment', 'extra', 'symlink'])
def test_root_kit_is_exact_and_before_state_writes(native, mutation):
    if mutation == 'body':
        (native.kit / 'broker.py').write_bytes(b'changed')
    elif mutation == 'manifest-deployment':
        p = native.kit / 'source-kit.json'
        data = json.loads(p.read_text())
        data['deploymentSha256'] = 'f'*64
        p.write_text(json.dumps(data))
    elif mutation == 'extra':
        (native.kit / 'unknown').write_bytes(b'unknown')
    else:
        p = native.kit / 'broker.py'
        p.unlink()
        p.symlink_to(native.f.config)
    with pytest.raises((source.CustodyError, OSError)):
        source.native_prepare_state(native.selected, native.identity)
    assert not source.NATIVE_STATE.exists() and not native.changes


def test_full_prepare_is_inode_bound_idempotent_and_has_no_jobs(native):
    descriptor = source.native_prepare_state(native.selected, native.identity)
    assert descriptor['deploymentSha256'] == native.f.value.checksum
    assert descriptor['phase'] == 'prepared-no-jobs'
    state = source.NATIVE_STATE
    assert set(p.name for p in state.iterdir()) == {'private','projection','results','proposals','custody.json'}
    assert stat.S_IMODE(state.stat().st_mode) == 0o700
    fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY)
    try:
        assert native.acl.acls[native.acl.key(fd)]['entries'] == [
            native.acl.entry(os.getuid()), native.acl.entry(60001)]
    finally:
        os.close(fd)
    changes, writes = list(native.changes), native.acl.writes
    assert source.native_prepare_state(native.selected, native.identity) == descriptor
    assert native.changes == changes and native.acl.writes == writes == 1
    assert not source.NATIVE_ENV.exists()


@pytest.mark.parametrize('kind', ['state', 'child', 'identity', 'record'])
def test_unbound_or_replaced_objects_are_not_adopted_or_repaired(native, kind):
    if kind == 'state':
        source.NATIVE_STATE.mkdir(mode=0o700)
    elif kind == 'identity':
        native.identity['groupGuid'] = str(uuid.uuid4()).upper()
    else:
        source.native_prepare_state(native.selected, native.identity)
        native.changes.clear()
        if kind == 'child':
            p = source.NATIVE_STATE / 'projection'
            p.rename(p.with_name('old-projection'))
            p.mkdir(mode=0o700)
        else:
            p = native.metadata / 'source-custody-prepare.json'
            data = json.loads(p.read_text())
            data['brokerGuid'] = str(uuid.uuid4()).upper()
            p.write_text(json.dumps(data))
    with pytest.raises(source.CustodyError):
        source.native_prepare_state(native.selected, native.identity)
    assert not native.changes


@pytest.mark.parametrize('change', ['inherit', 'unknown-flag', 'write', 'deny', 'third', 'same-uuid'])
def test_gate_requires_exact_noninheriting_list_search_only(native, change):
    source.native_prepare_state(native.selected, native.identity)
    fd = os.open(source.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        value = native.acl.acls[native.acl.key(fd)]
        if change == 'inherit':
            value['entries'][0]['flags'] = 1
        elif change == 'unknown-flag':
            value['flags'] = 1 << 31
        elif change == 'write':
            value['entries'][0]['mask'] |= 1 << 2
        elif change == 'deny':
            value['entries'][0]['tag'] = 2
        elif change == 'third':
            value['entries'].append(native.acl.entry(12345))
        else:
            native.acl.identities[60001] = native.acl.identities[os.getuid()]
        with pytest.raises(source.CustodyError):
            source.native_verify_gate(fd, os.getuid(), 60001)
    finally:
        os.close(fd)


def test_intent_bound_acl_failure_can_resume_but_unknown_acl_cannot(native):
    native.acl.fail_write = True
    with pytest.raises(source.CustodyError, match='acl-write-failed'):
        source.native_prepare_state(native.selected, native.identity)
    assert source.NATIVE_STATE.is_dir() and native.acl.writes==0
    prepared=json.loads((native.metadata/'source-custody-prepare.json').read_text())
    assert set(prepared['children'])=={'private','private/oauth','projection','results','proposals'}
    assert all(record['ready'] for record in prepared['children'].values())
    native.acl.fail_write = False
    assert source.native_prepare_state(native.selected, native.identity)['phase'] == 'prepared-no-jobs'
    fd = os.open(source.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        native.acl.acls[native.acl.key(fd)]['entries'][0]['uuid'] = b'x'*16
    finally:
        os.close(fd)
    writes = native.acl.writes
    with pytest.raises(source.CustodyError, match='acl-mismatch'):
        source.native_prepare_state(native.selected, native.identity)
    assert native.acl.writes == writes


def test_nonempty_or_unsafe_descendants_are_preserved_without_permission_repair(native):
    source.native_prepare_state(native.selected, native.identity)
    private = source.NATIVE_STATE / 'private'
    secret = private / 'invented-token.json'
    secret.write_bytes(b'{"invented":"never read by custody"}')
    secret.chmod(0o644)
    native.changes.clear()
    with pytest.raises(source.CustodyError, match='descendant-unsafe'):
        source.native_prepare_state(native.selected, native.identity)
    assert secret.read_bytes() == b'{"invented":"never read by custody"}'
    assert stat.S_IMODE(secret.stat().st_mode) == 0o644 and not native.changes


@pytest.mark.parametrize('conflict', ['uuid', 'shell', 'group', 'extra-authority'])
def test_identity_conflicts_refuse_before_any_directory_write(native, monkeypatch, conflict):
    record = native.records['Users']
    if conflict == 'uuid':
        record['GeneratedUID'] = [str(uuid.uuid4()).upper()]
    elif conflict == 'shell':
        record['UserShell'] = ['/bin/zsh']
    elif conflict == 'group':
        monkeypatch.setattr(source.grp, 'getgrall', lambda: [SimpleNamespace(gr_gid=20,gr_name='staff',gr_mem=[source.NATIVE_BROKER])])
    else:
        record['AuthenticationAuthority'] = ['unknown']
    monkeypatch.setattr(source.subprocess, 'run', lambda *a, **k: pytest.fail('no DS write allowed'))
    with pytest.raises(source.CustodyError):
        source.native_prepare_identity(native.selected)
    assert not native.changes


def test_uuid_bound_missing_attributes_resume_and_uid_is_last(native, monkeypatch):
    native.records['Groups'] = {'GeneratedUID': [native.identity['groupGuid']]}
    native.records['Users'] = {'GeneratedUID': [native.identity['userGuid']]}
    writes = []
    def run(arguments, **kwargs):
        assert arguments[:4] == ['/usr/bin/dscl','-plist','.','-create']
        assert kwargs['timeout'] == 20
        # Durable Root intent exists before every DirectoryService change.
        assert json.loads((native.metadata / 'source-identity.json').read_text()) == native.identity
        kind = arguments[4].split('/')[1]
        key, value = arguments[5:]
        native.records[kind][key] = [value]
        writes.append((kind, key))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(source.subprocess, 'run', run)
    assert source.native_prepare_identity(native.selected) == native.identity
    assert writes[-1] == ('Users','UniqueID')
    count = len(writes)
    assert source.native_prepare_identity(native.selected) == native.identity and len(writes) == count


def test_fresh_identity_intent_is_durable_before_first_ds_write_and_retry_keeps_uuid(native, monkeypatch):
    (native.metadata / 'source-identity.json').unlink()
    records = {'Groups': None, 'Users': None}
    monkeypatch.setattr(source, '_native_ds_record', lambda kind: copy.deepcopy(records[kind]))
    previous_group_lookup = source.grp.getgrnam
    def group_lookup(name):
        if name != source.NATIVE_BROKER:
            return previous_group_lookup(name)
        if records['Groups'] is None or 'PrimaryGroupID' not in records['Groups']:
            raise KeyError(name)
        return SimpleNamespace(gr_name=source.NATIVE_BROKER,gr_gid=int(records['Groups']['PrimaryGroupID'][0]),gr_mem=[])
    monkeypatch.setattr(source.grp,'getgrnam',group_lookup)
    previous_user_lookup = source.pwd.getpwnam
    def user_lookup(name):
        if name != source.NATIVE_BROKER:
            return previous_user_lookup(name)
        if records['Users'] is None or 'UniqueID' not in records['Users']:
            raise KeyError(name)
        return SimpleNamespace(pw_uid=int(records['Users']['UniqueID'][0]),pw_gid=int(records['Users']['PrimaryGroupID'][0]))
    monkeypatch.setattr(source.pwd,'getpwnam',user_lookup)
    writes = []
    fail = [True]
    def run(arguments, **kwargs):
        saved = source._native_identity_intent(json.loads((native.metadata / 'source-identity.json').read_text()))
        assert saved['id'] == 60000
        if fail[0]:
            fail[0] = False
            return SimpleNamespace(returncode=1)
        kind, key, value = arguments[4].split('/')[1], arguments[5], arguments[6]
        if records[kind] is None:
            records[kind] = {}
        records[kind][key] = [value]
        if kind == 'Users' and key == 'GeneratedUID':
            native.acl.identities[60000] = uuid.UUID(value).bytes
        if kind == 'Groups' and key == 'GeneratedUID':
            native.acl.group_identities[60000] = uuid.UUID(value).bytes
        writes.append((kind, key))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(source.subprocess, 'run', run)
    with pytest.raises(source.CustodyError, match='directory-write-failed'):
        source.native_prepare_identity(native.selected)
    saved = (native.metadata / 'source-identity.json').read_bytes()
    # The live named lookups above follow the recorded DS prefix.
    result = source.native_prepare_identity(native.selected)
    assert result == json.loads(saved) and (native.metadata / 'source-identity.json').read_bytes() == saved
    assert writes[-1] == ('Users', 'UniqueID')


def test_fresh_identity_never_adopts_a_preexisting_unreceipted_account(native, monkeypatch):
    (native.metadata / 'source-identity.json').unlink()
    monkeypatch.setattr(source.subprocess, 'run', lambda *a, **k: pytest.fail('must not write DS'))
    with pytest.raises(source.CustodyError, match='unmanaged-record'):
        source.native_prepare_identity(native.selected)
    assert not (native.metadata / 'source-identity.json').exists()


def test_interrupted_known_chown_prefix_resumes_exact_inode_without_repairing_unknown(native, monkeypatch):
    actual_chown = source.os.fchown
    failed = [False]
    def chown(fd, uid, gid):
        actual_chown(fd, uid, gid)
        if not failed[0]:
            failed[0] = True
            raise OSError('invented interruption after known inode ownership')
    monkeypatch.setattr(source.os, 'fchown', chown)
    with pytest.raises(OSError, match='invented interruption'):
        source.native_prepare_state(native.selected, native.identity)
    intent = json.loads((native.metadata / 'source-custody-prepare.json').read_text())
    assert intent['children']['private']['ready'] is False
    inode = (source.NATIVE_STATE / 'private').stat().st_ino
    monkeypatch.setattr(source.os, 'fchown', actual_chown)
    assert source.native_prepare_state(native.selected, native.identity)['phase'] == 'prepared-no-jobs'
    assert (source.NATIVE_STATE / 'private').stat().st_ino == inode


def test_creation_before_intent_publication_is_preserved_and_not_adopted(native, monkeypatch):
    save = source._native_save_at
    def interrupted(selected, directory, name, value, **kwargs):
        if name == 'source-custody-prepare.json' and value.get('gate') is not None:
            raise OSError('invented interruption before inode record')
        return save(selected, directory, name, value, **kwargs)
    monkeypatch.setattr(source, '_native_save_at', interrupted)
    with pytest.raises(OSError, match='before inode record'):
        source.native_prepare_state(native.selected, native.identity)
    inode = source.NATIVE_STATE.stat().st_ino
    monkeypatch.setattr(source, '_native_save_at', save)
    with pytest.raises(source.CustodyError, match='state-unmanaged'):
        source.native_prepare_state(native.selected, native.identity)
    assert source.NATIVE_STATE.stat().st_ino == inode and not native.changes and native.acl.writes == 0


@pytest.mark.parametrize('change', ['symlink','hardlink','acl','unknown-gate-file','public-write'])
def test_no_permission_repair_or_adoption_of_existing_descendant_changes(native, change):
    source.native_prepare_state(native.selected, native.identity)
    native.changes.clear()
    if change == 'unknown-gate-file':
        target = source.NATIVE_STATE / 'unknown'
        target.write_bytes(b'invented')
    elif change == 'public-write':
        target = source.NATIVE_STATE / 'projection'
        target.chmod(0o2770)
    else:
        target = source.NATIVE_STATE / 'private' / 'invented'
        if change == 'symlink':
            target.symlink_to(native.f.config)
        else:
            target.write_bytes(b'private invented')
            target.chmod(0o600)
            key = target.stat().st_dev, target.stat().st_ino
            native.owners[key] = (60001,60001)
            if change == 'hardlink':
                os.link(target, target.with_name('duplicate'))
            else:
                native.acl.acls[key] = {'entries':[native.acl.entry(os.getuid())]}
    before = target.lstat()
    with pytest.raises((source.CustodyError,OSError)):
        source.native_prepare_state(native.selected, native.identity)
    assert (target.lstat().st_mode,target.lstat().st_ino) == (before.st_mode,before.st_ino)
    assert not native.changes


def test_unrelated_staff_has_no_gate_principal_and_private_descendants_have_no_inheritance(native):
    source.native_prepare_state(native.selected, native.identity)
    fd = os.open(source.NATIVE_STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        entries = native.acl.acls[native.acl.key(fd)]['entries']
        assert {entry['uuid'] for entry in entries} == {
            native.acl.identities[os.getuid()],native.acl.identities[60001]}
        assert all(entry['tag'] == 1 and entry['mask'] == 10 and entry['flags'] == 0 for entry in entries)
    finally:
        os.close(fd)
    for name in ('private','private/oauth','projection','results','proposals'):
        fd = os.open(source.NATIVE_STATE/name, os.O_RDONLY | os.O_DIRECTORY)
        try:
            assert native.acl.key(fd) not in native.acl.acls
        finally:
            os.close(fd)


def test_ctypes_contract_uses_public_apple_signatures_without_opaque_layout(monkeypatch):
    class Function:
        pass
    names = ('acl_get_fd_np','acl_valid','acl_get_entry','acl_get_tag_type',
        'acl_get_permset_mask_np','acl_get_flagset_np','acl_get_flag_np','acl_get_qualifier',
        'acl_init','acl_create_entry','acl_set_tag_type','acl_set_qualifier',
        'acl_set_permset_mask_np','acl_set_fd_np','acl_free','mbr_uid_to_uuid','mbr_gid_to_uuid','mbr_check_membership')
    library = SimpleNamespace(**{name:Function() for name in names})
    monkeypatch.setattr(source,'sys',SimpleNamespace(platform='darwin'))
    def load(path, **kwargs):
        assert path == '/usr/lib/libSystem.B.dylib' and kwargs == {'use_errno':True}
        return library
    monkeypatch.setattr(ctypes,'CDLL',load)
    assert source._native_acl_library() is library
    assert library.acl_set_fd_np.argtypes == [ctypes.c_int,ctypes.c_void_p,ctypes.c_int]
    assert library.acl_get_permset_mask_np.argtypes == [ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint64)]
    assert library.acl_set_permset_mask_np.argtypes == [ctypes.c_void_p,ctypes.c_uint64]
    assert library.mbr_uid_to_uuid.argtypes == [ctypes.c_uint32,ctypes.c_void_p]
    assert library.mbr_gid_to_uuid.argtypes == [ctypes.c_uint32,ctypes.c_void_p]
    assert library.mbr_check_membership.argtypes == [ctypes.c_void_p,ctypes.c_void_p,ctypes.POINTER(ctypes.c_int)]
    assert library.acl_get_qualifier.restype is ctypes.c_void_p


def test_existing_linux_run_calls_all_map_without_changing_their_commands(tmp_path):
    units = tmp_path/'units'
    units.mkdir()
    for name in source.UNITS:
        (units/name).touch()
    paths = source.Paths(units=units, state=tmp_path/'state', env=tmp_path/'env')
    calls = []
    def run(arguments, timeout):
        calls.append((list(arguments),timeout))
        source.native_source_command(arguments,timeout)
        return 0
    source.start(run,direct=True)
    source.sync(run)
    source.quiesce_for_sign_in(paths,run)
    assert calls[:4] == [
        (['systemctl','daemon-reload'],30),
        (['systemctl','enable','--now','pixel-source-broker.timer'],30),
        (['systemctl','enable','--now','pixel-source-direct.path'],30),
        (['systemctl','start','--no-block','pixel-source-broker.service'],30)]
    assert (['systemctl','stop','pixel-source-action@*.service'],30) in calls
    assert (['systemctl','stop','pixel-source-reconcile@*.service'],30) in calls


@pytest.mark.parametrize('kind',['uid','gid'])
def test_broker_numeric_collision_refuses_before_ds_write(native,monkeypatch,kind):
    if kind == 'uid':
        monkeypatch.setattr(source.pwd,'getpwall',lambda:[SimpleNamespace(pw_uid=60001,pw_name='foreign')])
    else:
        monkeypatch.setattr(source.grp,'getgrall',lambda:[SimpleNamespace(gr_gid=60001,gr_name='foreign',gr_mem=[])])
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS write allowed'))
    with pytest.raises(source.CustodyError):
        source.native_prepare_identity(native.selected)


@pytest.mark.parametrize('entrypoint',['identity','state'])
def test_foreign_primary_broker_gid_refuses_before_any_ds_or_file_write(native,monkeypatch,entrypoint):
    monkeypatch.setattr(source.pwd,'getpwall',lambda:[SimpleNamespace(pw_uid=501,pw_gid=60001,pw_name='foreign')])
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('DS write reached before primary GID refusal'))
    monkeypatch.setattr(source,'_native_save_at',lambda *a,**k:pytest.fail('file write reached before primary GID refusal'))
    with pytest.raises(source.CustodyError,match='identity-'):
        if entrypoint=='identity':source.native_prepare_identity(native.selected)
        else:source.native_prepare_state(native.selected,native.identity)
    assert not source.NATIVE_STATE.exists() and not native.changes


def test_direct_named_group_membership_disagreement_refuses_before_any_ds_write(native,monkeypatch):
    original=source.grp.getgrnam
    monkeypatch.setattr(source.grp,'getgrnam',lambda name:SimpleNamespace(gr_name=source.NATIVE_BROKER,gr_gid=60001,gr_mem=['foreign'])
        if name==source.NATIVE_BROKER else original(name))
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('DS write reached before direct group refusal'))
    with pytest.raises(source.CustodyError,match='identity-'):
        source.native_prepare_identity(native.selected)


@pytest.mark.parametrize('entrypoint',['identity','state'])
@pytest.mark.parametrize('case',['nested-member','api-error','unknown-result','wrong-group-uuid'])
def test_resolved_dedicated_membership_refuses_before_any_ds_or_file_write(native,monkeypatch,entrypoint,case):
    foreign=SimpleNamespace(pw_uid=501,pw_gid=20,pw_name='foreign')
    monkeypatch.setattr(source.pwd,'getpwall',lambda:[foreign])
    pair=(source._native_uuid(native.acl,501),native.acl.group_identities[60001])
    if case=='nested-member':native.acl.memberships[pair]=1
    elif case=='api-error':native.acl.membership_error=errno.EIO
    elif case=='unknown-result':native.acl.memberships[pair]=2
    else:native.acl.group_identities[60001]=b'x'*16
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('DS write before membership proof'))
    monkeypatch.setattr(source,'_native_save_at',lambda *a,**k:pytest.fail('file write before membership proof'))
    with pytest.raises(source.CustodyError,match='identity-'):
        if entrypoint=='identity':source.native_prepare_identity(native.selected)
        else:source.native_prepare_state(native.selected,native.identity)
    assert not native.changes and not source.NATIVE_STATE.exists()
    if case!='wrong-group-uuid':assert pair in native.acl.membership_queries


def test_resolved_foreign_nonmember_allows_exact_preparation_after_proof(native,monkeypatch):
    monkeypatch.setattr(source.pwd,'getpwall',lambda:[SimpleNamespace(pw_uid=501,pw_gid=20,pw_name='foreign')])
    pair=(source._native_uuid(native.acl,501),native.acl.group_identities[60001])
    save=source._native_save_at
    def checked_save(*args,**kwargs):
        assert pair in native.acl.membership_queries
        return save(*args,**kwargs)
    monkeypatch.setattr(source,'_native_save_at',checked_save)
    assert source.native_prepare_state(native.selected,native.identity)['phase']=='prepared-no-jobs'
    assert pair in native.acl.membership_queries


def test_fresh_numeric_selection_excludes_other_users_primary_gids_before_intent_write(native,monkeypatch):
    (native.metadata/'source-identity.json').unlink()
    monkeypatch.setattr(source,'_native_ds_record',lambda kind:None)
    original_user,original_group=source.pwd.getpwnam,source.grp.getgrnam
    def missing_user(name):
        if name==source.NATIVE_BROKER:raise KeyError(name)
        return original_user(name)
    def missing_group(name):
        if name==source.NATIVE_BROKER:raise KeyError(name)
        return original_group(name)
    monkeypatch.setattr(source.pwd,'getpwnam',missing_user)
    monkeypatch.setattr(source.grp,'getgrnam',missing_group)
    monkeypatch.setattr(source.pwd,'getpwall',lambda:[SimpleNamespace(pw_uid=501,pw_gid=60000,pw_name='foreign')])
    attempted=[]
    def stop_before_write(selected,directory,name,value,**kwargs):
        attempted.append((name,value))
        raise OSError('intent intercepted before write')
    monkeypatch.setattr(source,'_native_save_at',stop_before_write)
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS write'))
    with pytest.raises(OSError,match='intercepted'):
        source.native_prepare_identity(native.selected)
    assert attempted[0][0]=='source-identity.json' and attempted[0][1]['id']==60001
    assert not (native.metadata/'source-identity.json').exists()


@pytest.mark.parametrize('field,value',[('gr_name','foreign'),('gr_gid',60002)])
def test_named_group_resolution_must_match_both_root_name_and_gid(native,monkeypatch,field,value):
    record={'gr_name':source.NATIVE_BROKER,'gr_gid':60001,'gr_mem':[]}
    record[field]=value
    original=source.grp.getgrnam
    monkeypatch.setattr(source.grp,'getgrnam',lambda name:SimpleNamespace(**record) if name==source.NATIVE_BROKER else original(name))
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS write'))
    with pytest.raises(source.CustodyError,match='membership-conflict'):
        source.native_prepare_identity(native.selected)


def test_same_name_with_foreign_uid_cannot_hide_a_primary_gid_collision(native,monkeypatch):
    monkeypatch.setattr(source.pwd,'getpwall',lambda:[SimpleNamespace(pw_uid=501,pw_gid=60001,pw_name=source.NATIVE_BROKER)])
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS write'))
    with pytest.raises(source.CustodyError,match='id-collision'):
        source.native_prepare_identity(native.selected)


@pytest.mark.parametrize('kind',['user','group'])
def test_fresh_intent_cannot_shadow_a_named_lookup_missing_from_local_ds(native,monkeypatch,kind):
    (native.metadata/'source-identity.json').unlink()
    monkeypatch.setattr(source,'_native_ds_record',lambda _kind:None)
    original_user,original_group=source.pwd.getpwnam,source.grp.getgrnam
    def missing_user(name):
        if name==source.NATIVE_BROKER:raise KeyError(name)
        return original_user(name)
    def missing_group(name):
        if name==source.NATIVE_BROKER:raise KeyError(name)
        return original_group(name)
    monkeypatch.setattr(source.pwd,'getpwnam',missing_user)
    monkeypatch.setattr(source.grp,'getgrnam',missing_group)
    if kind=='user':monkeypatch.setattr(source.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=500,pw_gid=20)
        if name==source.NATIVE_BROKER else original_user(name))
    else:monkeypatch.setattr(source.grp,'getgrnam',lambda name:SimpleNamespace(gr_name=source.NATIVE_BROKER,gr_gid=60001,gr_mem=[])
        if name==source.NATIVE_BROKER else original_group(name))
    monkeypatch.setattr(source,'_native_save_at',lambda *a,**k:pytest.fail('no intent write before cross-view refusal'))
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS write'))
    with pytest.raises(source.CustodyError,match='unmanaged-record'):
        source.native_prepare_identity(native.selected)


@pytest.mark.parametrize('kind',['listed-user','listed-group','orphan-member-name'])
def test_fresh_intent_refuses_enumerated_name_aliases_or_membership_before_writing(native,monkeypatch,kind):
    (native.metadata/'source-identity.json').unlink()
    monkeypatch.setattr(source,'_native_ds_record',lambda _kind:None)
    original_user,original_group=source.pwd.getpwnam,source.grp.getgrnam
    def absent_user(name):
        if name==source.NATIVE_BROKER:raise KeyError(name)
        return original_user(name)
    def absent_group(name):
        if name==source.NATIVE_BROKER:raise KeyError(name)
        return original_group(name)
    monkeypatch.setattr(source.pwd,'getpwnam',absent_user)
    monkeypatch.setattr(source.grp,'getgrnam',absent_group)
    if kind=='listed-user':monkeypatch.setattr(source.pwd,'getpwall',lambda:[SimpleNamespace(pw_name=source.NATIVE_BROKER,pw_uid=500,pw_gid=20)])
    else:monkeypatch.setattr(source.grp,'getgrall',lambda:[SimpleNamespace(gr_name=source.NATIVE_BROKER if kind=='listed-group' else 'foreign',
        gr_gid=60000,gr_mem=[] if kind=='listed-group' else [source.NATIVE_BROKER])])
    monkeypatch.setattr(source,'_native_save_at',lambda *a,**k:pytest.fail('no intent write'))
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS write'))
    with pytest.raises(source.CustodyError,match='unmanaged-record'):
        source.native_prepare_identity(native.selected)


@pytest.mark.parametrize('entrypoint',['identity','state'])
def test_enumerated_dedicated_group_reader_is_not_hidden_by_empty_direct_lookup(native,monkeypatch,entrypoint):
    monkeypatch.setattr(source.grp,'getgrall',lambda:[SimpleNamespace(gr_name=source.NATIVE_BROKER,gr_gid=60001,gr_mem=['foreign'])])
    # Deliberately absent from the passwd enumeration; the positive group record
    # must suffice to refuse, even though the direct getgrnam view is empty.
    assert source.pwd.getpwall()==[] and source.grp.getgrnam(source.NATIVE_BROKER).gr_mem==[]
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('DS write before enumerated reader refusal'))
    monkeypatch.setattr(source,'_native_save_at',lambda *a,**k:pytest.fail('file write before enumerated reader refusal'))
    with pytest.raises(source.CustodyError,match='identity-'):
        if entrypoint=='identity':source.native_prepare_identity(native.selected)
        else:source.native_prepare_state(native.selected,native.identity)
    assert not source.NATIVE_STATE.exists() and not native.changes


@pytest.mark.parametrize('change',['wrong-gid-for-name','wrong-name-for-gid','nonlist-empty'])
def test_enumerated_dedicated_identity_must_match_empty_exact_group(native,monkeypatch,change):
    fields={'gr_name':source.NATIVE_BROKER,'gr_gid':60001,'gr_mem':[]}
    if change=='wrong-gid-for-name':fields['gr_gid']=60002
    elif change=='wrong-name-for-gid':fields['gr_name']='foreign'
    else:fields['gr_mem']=None
    monkeypatch.setattr(source.grp,'getgrall',lambda:[SimpleNamespace(**fields)])
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS write before enumeration refusal'))
    with pytest.raises(source.CustodyError,match='membership-conflict'):
        source.native_prepare_identity(native.selected)


def test_direct_named_group_requires_an_actual_empty_member_list(native,monkeypatch):
    original=source.grp.getgrnam
    monkeypatch.setattr(source.grp,'getgrnam',lambda name:SimpleNamespace(gr_name=source.NATIVE_BROKER,gr_gid=60001,gr_mem=None)
        if name==source.NATIVE_BROKER else original(name))
    with pytest.raises(source.CustodyError,match='membership-conflict'):
        source.native_prepare_identity(native.selected)


def test_known_owner_group_omitted_from_enumeration_is_reserved_before_identity_intent(native,monkeypatch):
    # Actual Root qualifier/runtime/bundle/loaded-definition path; only the
    # selected group's account-database metadata is explicitly simulated.
    f=native.f
    definition=plistlib.loads(f.plist.read_bytes())
    definition['GroupName']='qa-omitted-owner-group'
    f.plist.write_bytes(plistlib.dumps(definition))
    original_group,original_gid=source.grp.getgrnam,source.grp.getgrgid
    original_user=source.pwd.getpwnam
    owner_user=original_user(f.bridge.gateway_owner)
    monkeypatch.setattr(source.pwd,'getpwnam',lambda name:SimpleNamespace(pw_uid=owner_user.pw_uid,pw_gid=60000,
        pw_name=owner_user.pw_name,pw_dir=owner_user.pw_dir) if name==f.bridge.gateway_owner else original_user(name))
    monkeypatch.setattr(source.grp,'getgrnam',lambda name:SimpleNamespace(gr_name=name,gr_gid=60000,gr_mem=[])
        if name=='qa-omitted-owner-group' else original_group(name))
    monkeypatch.setattr(source.grp,'getgrgid',lambda gid:SimpleNamespace(gr_name='qa-omitted-owner-group',gr_gid=60000,gr_mem=[])
        if gid==60000 else original_gid(gid))
    f.bridge.owner=SimpleNamespace(pw_uid=os.getuid(),pw_gid=60000,pw_name=f.bridge.owner.pw_name)
    f.bridge.gateway_process={**f.bridge.gateway_process,'gid':60000}
    current=f.bridge.connection_deployment()
    selected=source.native_source_selection(f.bridge,current)
    assert current.document['ownerGid']==60000
    manifest=json.loads((native.kit/'source-kit.json').read_text())
    manifest['deploymentSha256']=current.checksum
    (native.kit/'source-kit.json').write_text(json.dumps(manifest))
    (native.metadata/'source-identity.json').unlink()
    native.records['Groups']=None
    native.records['Users']=None
    assert source.pwd.getpwall()==[] and source.grp.getgrall()==[]
    attempts=[]
    def intercepted(selected,directory,name,value,**kwargs):
        attempts.append((name,value))
        raise OSError('identity intent intercepted before file write')
    monkeypatch.setattr(source,'_native_save_at',intercepted)
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS mutation'))
    with pytest.raises(OSError,match='intercepted'):
        source.native_prepare_identity(selected)
    assert attempts[0][0]=='source-identity.json'
    assert attempts[0][1]['id'] not in {current.document['ownerUid'],current.document['ownerGid']}
    assert not (native.metadata/'source-identity.json').exists() and not native.changes



def env_file(native):
    path=source.NATIVE_ENV
    path.write_bytes(b"PIXEL_CALENDAR_DIRECT_ENABLED='0'\n")
    path.chmod(0o600)
    info=path.stat()
    native.acl.acls[(info.st_dev,info.st_ino)]={'entries':[native.acl.entry(60001,mask=2)],'flags':0}
    return path


def test_fixed_native_env_is_inside_root_gate_and_not_the_legacy_location():
    paths=source.NativeSourcePaths()
    assert paths.env==paths.state/'source-broker.env'
    assert paths.env!=source.NATIVE_LEGACY_ENV


def test_env_fd_has_only_broker_user_read_and_does_not_depend_on_group_enumeration(native,monkeypatch):
    source.native_prepare_state(native.selected,native.identity)
    path=env_file(native)
    monkeypatch.setattr(source.pwd,'getpwall',lambda:pytest.fail('no user enumeration for fd custody'))
    monkeypatch.setattr(source.grp,'getgrall',lambda:pytest.fail('no group enumeration for fd custody'))
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    gate=os.open(source.NATIVE_STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    writes=native.acl.writes
    try:
        stamp=source.native_verify_env_fd(fd,60001)
        assert stamp[:2]==(path.stat().st_dev,path.stat().st_ino)
        assert source._native_env_at(gate,os.getuid(),60001,required=True)==stamp
        assert native.acl.acls[native.acl.key(fd)]['entries']==[native.acl.entry(60001,mask=2)]
        # A file ACL may not weaken the unchanged directory reader(mask10).
        with pytest.raises(source.CustodyError,match='acl-rights'):
            source._native_acl_entries(fd)
        source.native_verify_gate(gate,os.getuid(),60001)
    finally:
        os.close(fd)
        os.close(gate)
    assert native.acl.writes==writes


@pytest.mark.parametrize('case',['missing','owner-reader','duplicate','write','directory-mask','inherit','header-flag','deny'])
def test_env_fd_rejects_any_acl_beyond_singleton_broker_read(native,case):
    source.native_prepare_state(native.selected,native.identity)
    path=env_file(native)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        key=native.acl.key(fd)
        if case=='missing':native.acl.acls.pop(key)
        else:
            value=native.acl.acls[key]
            if case=='owner-reader':value['entries'][0]=native.acl.entry(os.getuid(),mask=2)
            elif case=='duplicate':value['entries'].append(native.acl.entry(60001,mask=2))
            elif case=='write':value['entries'][0]['mask']=6
            elif case=='directory-mask':value['entries'][0]['mask']=10
            elif case=='inherit':value['entries'][0]['flags']=1<<5
            elif case=='header-flag':value['flags']=1<<31
            else:value['entries'][0]['tag']=2
        with pytest.raises(source.CustodyError):source.native_verify_env_fd(fd,60001)
    finally:os.close(fd)


@pytest.mark.parametrize('case',['mode640','owner','group','hardlink','empty','oversize','fifo'])
def test_env_fd_requires_root_singleton_regular_private_bounded_file(native,case):
    source.native_prepare_state(native.selected,native.identity)
    path=env_file(native)
    if case=='mode640':path.chmod(0o640)
    elif case in ('owner','group'):
        info=path.stat()
        native.owners[(info.st_dev,info.st_ino)]=(os.getuid(),0) if case=='owner' else (0,60001)
    elif case=='hardlink':os.link(path,path.with_name('duplicate-env'))
    elif case=='empty':path.write_bytes(b'')
    elif case=='oversize':path.write_bytes(b'x'*(source.MAX_RESULT_BYTES+1))
    else:
        path.unlink()
        os.mkfifo(path,0o600)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        with pytest.raises(source.CustodyError,match='env-unsafe'):source.native_verify_env_fd(fd,60001)
    finally:os.close(fd)


def test_env_fd_detects_actual_file_change_during_acl_validation(native,monkeypatch):
    source.native_prepare_state(native.selected,native.identity)
    path=env_file(native)
    qualifier=native.acl.acl_get_qualifier
    def changed(ptr):
        result=qualifier(ptr)
        with path.open('ab') as stream:stream.write(b'#changed\n')
        return result
    monkeypatch.setattr(native.acl,'acl_get_qualifier',changed)
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    try:
        with pytest.raises(source.CustodyError,match='env-changed'):source.native_verify_env_fd(fd,60001)
    finally:os.close(fd)


def test_fixed_env_reader_is_optional_until_publication_and_never_follows_alias(native):
    source.native_prepare_state(native.selected,native.identity)
    gate=os.open(source.NATIVE_STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        assert source._native_env_at(gate,os.getuid(),60001) is None
        with pytest.raises(source.CustodyError,match='env-unavailable'):
            source._native_env_at(gate,os.getuid(),60001,required=True)
        source.NATIVE_ENV.symlink_to(native.f.config)
        before=native.f.config.read_bytes()
        with pytest.raises(OSError):source._native_env_at(gate,os.getuid(),60001)
        assert native.f.config.read_bytes()==before
    finally:os.close(gate)


@pytest.mark.parametrize('kind',['file','symlink','directory','fifo'])
def test_legacy_outside_gate_env_is_preserved_and_refused_before_mutations(native,monkeypatch,kind):
    path=source.NATIVE_LEGACY_ENV
    if kind=='file':path.write_bytes(b'invented preserved legacy config')
    elif kind=='symlink':path.symlink_to(native.f.config)
    elif kind=='directory':path.mkdir()
    else:os.mkfifo(path,0o600)
    original=path.lstat()
    monkeypatch.setattr(source.subprocess,'run',lambda *a,**k:pytest.fail('no DS mutation for legacy residue'))
    monkeypatch.setattr(source,'_native_save_at',lambda *a,**k:pytest.fail('no state write for legacy residue'))
    for operation in (lambda:source.native_prepare_identity(native.selected),
                      lambda:source.native_prepare_state(native.selected,native.identity)):
        with pytest.raises(source.CustodyError,match='legacy-env-preserved'):operation()
    assert (path.lstat().st_dev,path.lstat().st_ino,path.lstat().st_mode)==(original.st_dev,original.st_ino,original.st_mode)
    assert not source.NATIVE_STATE.exists() and not native.changes and native.acl.writes==0


def test_preparer_does_not_adopt_an_env_found_in_an_unfinished_gate_prefix(native):
    native.acl.fail_write=True
    with pytest.raises(source.CustodyError,match='acl-write-failed'):
        source.native_prepare_state(native.selected,native.identity)
    path=env_file(native)
    before=path.read_bytes()
    changes=list(native.changes)
    native.acl.fail_write=False
    with pytest.raises(source.CustodyError,match='state-unmanaged'):
        source.native_prepare_state(native.selected,native.identity)
    assert native.acl.writes==0,'unknown env must not receive a newly accessible gate'
    assert path.read_bytes()==before and native.changes==changes



def test_unpublished_registered_gate_stays_closed_when_known_child_is_unsafe(native):
    native.acl.fail_write=True
    with pytest.raises(source.CustodyError,match='acl-write-failed'):
        source.native_prepare_state(native.selected,native.identity)
    native.acl.fail_write=False
    gate=os.open(source.NATIVE_STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    metadata=os.open(native.metadata,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        # Real Root record publication binds this known newly created child FD.
        try:os.mkdir('private',mode=0o700,dir_fd=gate)
        except FileExistsError:pass
        child=os.open('private',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=gate)
        try:
            info=os.fstat(child)
            intent,raw=source._native_json_at(metadata,'source-custody-prepare.json')
            # A publication interrupted before the ready record may retain an
            # already chowned inode. Both old/new publication orders construct
            # this same supported inode-bound prefix; no authority is faked.
            intent['children']['private']={'inode':[info.st_dev,info.st_ino],'ready':False}
            source._native_save_at(native.selected,metadata,'source-custody-prepare.json',intent,previous=raw)
            os.fchmod(child,0o777)  # Unsupported prefix; never repair it.
        finally:os.close(child)
    finally:
        os.close(metadata)
        os.close(gate)
    changes=list(native.changes)
    with pytest.raises(source.CustodyError,match='descendant-unmanaged'):
        source.native_prepare_state(native.selected,native.identity)
    assert native.acl.writes==0,'a known unsafe child must be refused before opening the gate'
    assert native.changes==changes and stat.S_IMODE((source.NATIVE_STATE/'private').stat().st_mode)==0o777



def test_gate_publication_is_after_complete_children_descriptor_and_data_tree(native,monkeypatch):
    publish=source._native_write_gate_acl
    walk=source._native_verify_data_tree
    checked=[]
    def checked_walk(fd,**kwargs):
        result=walk(fd,**kwargs)
        checked.append(os.fstat(fd).st_ino)
        return result
    def checked_publish(selection,fd,owner_uid,broker_uid):
        record=json.loads((native.metadata/'source-custody-prepare.json').read_text())
        assert set(record['children'])=={'private','private/oauth','projection','results','proposals'}
        assert all(child['ready'] for child in record['children'].values())
        assert (source.NATIVE_STATE/'custody.json').is_file()
        assert len(checked)>=4 and native.acl.writes==0
        return publish(selection,fd,owner_uid,broker_uid)
    monkeypatch.setattr(source,'_native_verify_data_tree',checked_walk)
    monkeypatch.setattr(source,'_native_write_gate_acl',checked_publish)
    assert source.native_prepare_state(native.selected,native.identity)['phase']=='prepared-no-jobs'
    assert native.acl.writes==1


def test_lost_final_root_currentness_leaves_prepared_gate_unpublished(native,monkeypatch):
    selected=source._native_selected
    def lose_after_preparation(value):
        result=selected(value)  # Actual qualifier still runs before fault.
        if (source.NATIVE_STATE/'custody.json').exists():
            raise source.CustodyError('injected-final-currentness-lost')
        return result
    monkeypatch.setattr(source,'_native_selected',lose_after_preparation)
    with pytest.raises(source.CustodyError,match='final-currentness-lost'):
        source.native_prepare_state(native.selected,native.identity)
    assert native.acl.writes==0
    assert (source.NATIVE_STATE/'custody.json').is_file()
    monkeypatch.setattr(source,'_native_selected',selected)
    assert source.native_prepare_state(native.selected,native.identity)['phase']=='prepared-no-jobs'
    assert native.acl.writes==1


def test_already_published_gate_is_preserved_when_child_later_becomes_invalid(native):
    source.native_prepare_state(native.selected,native.identity)
    fd=os.open(source.NATIVE_STATE,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:key=native.acl.key(fd)
    finally:os.close(fd)
    before=copy.deepcopy(native.acl.acls[key])
    (source.NATIVE_STATE/'private').chmod(0o777)
    with pytest.raises(source.CustodyError,match='descendant-changed'):
        source.native_prepare_state(native.selected,native.identity)
    assert native.acl.writes==1 and native.acl.acls[key]==before
    assert stat.S_IMODE((source.NATIVE_STATE/'private').stat().st_mode)==0o777
