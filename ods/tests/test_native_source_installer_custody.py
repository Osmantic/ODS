"""Fixed installer custody readers with real confined files; no Mac/provider claim."""
# ruff: noqa: E402,F401,F811
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs metadata driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_executor import executor, qualification_operation
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native, launchctl_system, LAUNCHD_OVERRIDES
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from test_native_source_publication import material, publication, helper
from test_native_source_installer_first_kit import first_target
from pixel_connections import custody as c
from pixel_provider import runtime_custody as rc
from pixel_access_bridge import LaunchdAccessBridge
import pixel_macos_custody as mac


def sha(body):
    return hashlib.sha256(body).hexdigest()


@pytest.fixture
def absent(native, monkeypatch):
    n = native
    for path in n.kit.iterdir():
        path.unlink()
    n.kit.rmdir()
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: '01234567-89AB-4DEF-8123-0123456789AB')
    calls = []
    def command(argv, timeout=10):
        calls.append(argv)
        if argv == ['/bin/launchctl', 'print', 'system']:
            return launchctl_system()
        if argv == ['/bin/ps', '-axo', 'pid=,uid=']:
            return '1 0\n42 1000\n'
        raise AssertionError('unexpected command ' + repr(argv))
    monkeypatch.setattr(n.f.bridge, 'command', command)
    n.census = calls
    return n


def test_identity_only_inventory_preserves_exact_record_and_rejects_any_private_state(absent):
    n = absent
    original = (n.metadata / 'source-identity.json').read_bytes()
    kit, files, identity, witness = c._native_installer_absence_state(n.f.bridge)
    assert kit is None and identity == {'kind': 'retained', 'record': n.identity}
    assert files['source-identity.json']['sha256'] == sha(original)
    assert witness['brokerUid'] == n.identity['id'] and witness['brokerProcesses'] == 0
    c.NATIVE_STATE.mkdir(mode=0o700)
    with pytest.raises(c.CustodyError, match='private-state-present'):
        c._native_installer_absence_state(n.f.bridge)
    assert (n.metadata / 'source-identity.json').read_bytes() == original


def test_retained_identity_bootstrap_uses_actual_fixed_inventory_without_runtime_capability(absent, monkeypatch):
    n = absent
    commands = []
    def run(argv, **kwargs):
        commands.append(argv)
        assert kwargs['stdin'] is c.subprocess.DEVNULL and kwargs['timeout'] == 10
        output = launchctl_system() if argv == ['/bin/launchctl', 'print', 'system'] else '1 0\n42 1000\n'
        return SimpleNamespace(returncode=0, stdout=output)
    monkeypatch.setattr(c.subprocess, 'run', run)
    result = c.native_retained_identity_only(owner_uid=n.f.bridge.owner.pw_uid, owner_gid=n.f.bridge.owner.pw_gid)
    assert result['record'] == n.identity and result['witness']['brokerUid'] == n.identity['id']
    assert commands == [['/bin/launchctl', 'print', 'system'], ['/bin/ps', '-axo', 'pid=,uid=']] * 2
    assert not c.NATIVE_STATE.exists() and not c.NATIVE_KIT.exists()


@pytest.mark.parametrize('change', ['unknown-metadata', 'loaded-label', 'broker-process', 'partial-ds'])
def test_absence_inventory_never_uses_missing_source_data_to_waive_custody(absent, monkeypatch, change):
    n = absent
    if change == 'unknown-metadata':
        (n.metadata / 'source-unrecognized.json').write_text('{}')
    elif change == 'partial-ds':
        n.records['Users'] = None
    else:
        original = n.f.bridge.command
        def changed(argv, timeout=10):
            if change == 'loaded-label' and argv == ['/bin/launchctl', 'print', 'system']:
                return launchctl_system(c.NATIVE_LABEL + 'sync')
            if change == 'broker-process' and argv == ['/bin/ps', '-axo', 'pid=,uid=']:
                return '1 0\n42 ' + str(n.identity['id']) + '\n'
            return original(argv, timeout=timeout)
        monkeypatch.setattr(n.f.bridge, 'command', changed)
    before = (n.metadata / 'source-identity.json').read_bytes()
    codes = {'loaded-label': 'native-source-installer-loaded-source-preserved',
        'broker-process': 'native-source-installer-process-preserved'}
    with pytest.raises(c.CustodyError, match=codes.get(change)):
        c._native_installer_absence_state(n.f.bridge)
    assert (n.metadata / 'source-identity.json').read_bytes() == before


def test_absence_inventory_ignores_labels_only_in_launchd_override_table(absent):
    # The fleet Mac kept these `disabled services` entries after every ODS job
    # was booted out. A whole-output search refused them as loaded jobs.
    n = absent
    raw = launchctl_system()
    assert c.NATIVE_LABEL in raw and all(label in raw for label in LAUNCHD_OVERRIDES)
    assert not any(label.startswith('com.ods.') for label in c.native_launchd_loaded_labels(raw))
    kit, files, identity, witness = c._native_installer_absence_state(n.f.bridge)
    assert identity['kind'] == 'retained' and witness['brokerProcesses'] == 0
    assert ['/bin/launchctl', 'print', 'system'] in n.census


def test_launchd_census_reads_only_the_loaded_services_table():
    loaded = c.native_launchd_loaded_labels(launchctl_system('com.ods.pixel-access-relay', c.NATIVE_LABEL + 'sync'))
    # Exact row labels: a loaded label's prefix is not itself loaded.
    assert {'com.ods.pixel-access-relay', c.NATIVE_LABEL + 'sync', 'com.apple.kernelmanager_helper'} <= loaded
    assert 'com.ods.pixel-access' not in loaded and 'com.ods.pixel-native-gateway' not in loaded
    # ODS names in every other table stay unloaded.
    raw = launchctl_system().replace('\t\tpid/466\n', '\t\tpid/466\n\t\tcom.ods.pixel-access\n').replace(
        'com.apple.logd.admin\n', 'com.apple.logd.admin\n\t\t 0x1603    M   A   ' + c.NATIVE_LABEL + 'direct\n').replace(
        'com.apple.MessagesBlastDoorService\n', 'com.apple.MessagesBlastDoorService\n\t\tcom.ods.pixel-native-manager\n')
    assert not any(label.startswith('com.ods.') for label in c.native_launchd_loaded_labels(raw))
    # The production reader receives the bridge's stripped stdout.
    assert c.native_launchd_loaded_labels(launchctl_system().strip()) == c.native_launchd_loaded_labels(launchctl_system())


def _one_row_changed(row):
    lines = launchctl_system().splitlines(keepends=True)
    index = next(i for i, line in enumerate(lines) if line.endswith('com.apple.wifiFirmwareLoader\n'))
    lines[index] = row
    return ''.join(lines)


@pytest.mark.parametrize('raw', [
    '', 'system = {\n}\n', 'gui/501 = {\n\tservices = {\n\t\t       0      - \tcom.apple.x\n\t}\n}\n',
    launchctl_system().replace('\tservices = {\n', '\tservice = {\n'),
    launchctl_system().replace('\tunmanaged processes = {\n', '\tservices = {\n'),
    launchctl_system().rsplit('}', 1)[0],
    'system = {\n\tservices = {\n\t}\n}\n',
    'system = {\n\tservices = {\n\t\t       0      - \tcom.apple.x\n}\n',
    _one_row_changed(' ' + c.NATIVE_LABEL + 'sync = {}\n'),
    _one_row_changed('\t\t' + c.NATIVE_LABEL + 'sync\n'),
    _one_row_changed('\t\t       -      - \t' + c.NATIVE_LABEL + 'sync\n'),
    _one_row_changed('\t\t\t       0      - \t' + c.NATIVE_LABEL + 'sync\n'),
    _one_row_changed('\t\t       0      - \tcom.apple.kernelmanager_helper\n'),
    _one_row_changed('\t\t       0      - \tcom.ods.pixel-access (pending)\n'),
    launchctl_system() + ' ' * (1024 * 1024)], ids=[
    'empty', 'no-services-table', 'other-domain', 'renamed-table', 'two-tables', 'unterminated-output',
    'empty-table', 'unterminated-table', 'assignment-row', 'bare-label-row', 'dash-pid-row', 'nested-row',
    'duplicate-row', 'annotated-label-row', 'oversized'])
def test_launchd_census_fails_closed_on_unknown_layout(raw):
    with pytest.raises(c.CustodyError, match='native-source-launchd-census-unavailable'):
        c.native_launchd_loaded_labels(raw)


def test_absence_inventory_accepts_a_macos_nobody_process(absent, monkeypatch):
    # macOS `ps` prints the nobody account as uid -2 (for example /usr/libexec/dhcp6d).
    # Such a row is an ordinary unrelated process, not an unreadable census.
    n = absent
    original = n.f.bridge.command
    def with_nobody(argv, timeout=10):
        if argv == ['/bin/ps', '-axo', 'pid=,uid=']:
            return '1 0\n42 1000\n17200    -2\n'
        return original(argv, timeout=timeout)
    monkeypatch.setattr(n.f.bridge, 'command', with_nobody)
    kit, files, identity, witness = c._native_installer_absence_state(n.f.bridge)
    assert identity['kind'] == 'retained' and witness['brokerProcesses'] == 0


@pytest.fixture
def withdrawal(executor, monkeypatch):
    n = executor
    monkeypatch.setattr(n.f.bridge, 'state', n.metadata, raising=False)
    monkeypatch.setattr(c, 'NATIVE_RUNTIMES', n.f.root.parent)
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_DESCRIPTOR', rc.DESCRIPTOR)
    descriptor = json.loads(rc.DESCRIPTOR.read_bytes())
    monkeypatch.setattr(c, 'NATIVE_RUNTIME_MANIFEST', Path(descriptor['manifest']))
    program = n.world / 'program'
    program.mkdir(mode=0o755)
    monkeypatch.setattr(c, 'NATIVE_PROGRAM', program)
    original_directory = mac.protected_directory
    code_parents = {Path(path).parent for path in c.native_installer_source_family()
                    if Path(path).is_relative_to(program)}
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) not in code_parents:
            with original_directory(path, **kwargs) as fd:
                yield fd
            return
        assert not kwargs
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            assert (info.st_uid, info.st_gid) == (0, 0) and not info.st_mode & 0o022
            c._native_no_acl(fd)
            yield fd
        finally:
            os.close(fd)
    monkeypatch.setattr(mac, 'protected_directory', directory)
    original_read = mac.protected_bytes
    def protected_bytes(path, *, limit=8 * 1024 * 1024):
        body = original_read(path)
        assert len(body) <= limit
        return body
    monkeypatch.setattr(mac, 'protected_bytes', protected_bytes)
    doc = c._native_selected(n.selected, _publication=True)
    candidate = 'a' * 64 if doc['bundleSha256'] != 'a' * 64 else 'b' * 64
    rows = []
    for name in sorted(c.native_installer_source_family()):
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        before = path.read_bytes() if path.exists() else None
        after = b'declared candidate code:' + name.encode()
        rows.append({'path': name, 'mode': 0o600 if name.endswith('/source-runtime-installation.json') else 0o644,
            'gid': 0, 'before': None if before is None else base64.b64encode(before).decode(),
            'beforeSha256': None if before is None else sha(before),
            'after': base64.b64encode(after).decode(), 'afterSha256': sha(after)})
    family = sha(json.dumps([[row['path'], row['mode'], row['gid'], row['afterSha256']] for row in rows],
        separators=(',', ':'), ensure_ascii=True).encode())
    owner = n.f.bridge.owner
    context = {'schemaVersion': 2, 'owner': {'uid': owner.pw_uid, 'gid': owner.pw_gid,
        'name': owner.pw_name, 'home': str(n.f.bridge.home)}, 'runtime_bundle': {'digest': candidate},
        'upgrade_qualification': {'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate},
        'source_lifecycle': {'schemaVersion': 1, 'kind': 'ods-native-source-installer-intent', 'mode': 'withdrawal',
            'nonce': '1' * 32, 'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate,
            'ownerUid': owner.pw_uid, 'ownerGid': owner.pw_gid, 'familySha256': family}}
    body = c._native_encoded(context)
    n.transaction = {'kind': 'native-installer-upgrade', 'transaction': sha(body),
        'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate}
    context_path = n.metadata / ('runtime-upgrade-context-' + candidate + '.json')
    context_path.write_bytes(body)
    context_path.chmod(0o600)
    n.intent = helper.installer_withdrawal_intent(n.selected, transaction=n.transaction)
    value = {'schemaVersion': 4, 'currentDigest': doc['bundleSha256'], 'candidateDigest': candidate,
        'phase': 'prepared', 'files': rows, 'sourceAddition': {'schemaVersion': 1,
            'kind': 'ods-native-source-installer-addition', 'contextSha256': n.transaction['transaction'],
            'familySha256': family, 'proof': n.intent, 'absentPaths': sorted(row['path'] for row in rows if row['before'] is None)}}
    n.upgrade = n.metadata / 'runtime-upgrade.json'
    n.upgrade.write_bytes(c._native_encoded(value))
    n.upgrade.chmod(0o600)
    n.receipt = helper.withdraw_installer_generation(n.selected, transaction=n.transaction, intent=n.intent)
    return n


def test_fresh_withdrawal_verifier_reopens_old_immutable_source_during_mixed_live_images(withdrawal):
    n = withdrawal
    before = c.NATIVE_ENV.stat().st_ino
    assert c.native_verify_installer_withdrawal(n.f.bridge, n.receipt, transaction=n.transaction) == n.receipt
    value = json.loads(n.upgrade.read_bytes())
    value['sourceAddition']['receipt'] = n.receipt
    value['phase'] = 'replacing-files'
    n.upgrade.write_bytes(c._native_encoded(value))
    row = next(row for row in value['files'] if row['path'] == str(c.NATIVE_RUNTIME_DESCRIPTOR))
    c.NATIVE_RUNTIME_DESCRIPTOR.write_bytes(base64.b64decode(row['after']))
    assert c.native_verify_installer_withdrawal(n.f.bridge, n.receipt, transaction=n.transaction) == n.receipt
    assert c.NATIVE_ENV.stat().st_ino == before
    c.NATIVE_RUNTIME_DESCRIPTOR.write_bytes(b'unknown mixed image')
    with pytest.raises(c.CustodyError, match='file-image-changed'):
        c.native_verify_installer_withdrawal(n.f.bridge, n.receipt, transaction=n.transaction)


def cold_file_bridge(n, monkeypatch):
    """Real constructor; deliberately no live gateway or discovery fields."""
    cold = LaunchdAccessBridge(str(n.f.bridge.install), 'synthetic-root-key',
        gateway_target='system/com.ods.pixel-native-gateway',
        gateway_plist='/Library/LaunchDaemons/com.ods.pixel-native-gateway.plist',
        gateway_process=dict(n.f.bridge.gateway_process), state=n.metadata,
        gateway_binding=None, installed_binary=n.f.bridge.binary, gateway_owner=n.f.bridge.gateway_owner)
    assert not any(hasattr(cold, name) for name in ('owner', 'home', '_runtime_config_path'))
    monkeypatch.setattr(cold, 'command', n.f.bridge.command)
    inspect = rc.RuntimeCustody.inspect
    def inspect_runtime(self, *args, **kwargs):
        if self.bridge is cold:
            pytest.fail('mixed stopped-Core proof tried to select a live runtime')
        return inspect(self, *args, **kwargs)
    monkeypatch.setattr(rc.RuntimeCustody, 'inspect', inspect_runtime)
    deployment = LaunchdAccessBridge.connection_deployment
    def select(self):
        if self is cold:
            pytest.fail('file proof minted a ConnectionDeployment')
        return deployment(self)
    monkeypatch.setattr(LaunchdAccessBridge, 'connection_deployment', select)
    return cold


@pytest.mark.parametrize('drift', [False, True])
def test_actual_cold_constructor_withdrawal_proof_uses_fresh_ds_without_discovery(withdrawal, monkeypatch, drift):
    n = withdrawal
    journal = json.loads(n.upgrade.read_bytes())
    journal['sourceAddition']['receipt'] = n.receipt
    journal['phase'] = 'replacing-files'
    n.upgrade.write_bytes(c._native_encoded(journal))
    row = next(row for row in journal['files'] if row['path'] == str(c.NATIVE_RUNTIME_DESCRIPTOR))
    c.NATIVE_RUNTIME_DESCRIPTOR.write_bytes(base64.b64decode(row['after']))
    cold = cold_file_bridge(n, monkeypatch)
    before = {path: (path.read_bytes(), path.stat().st_ino) for path in n.metadata.iterdir() if path.is_file()}
    if drift:
        lookup = c.pwd.getpwnam
        def owner(name):
            value = lookup(name)
            if name == cold.gateway_owner:
                return SimpleNamespace(pw_name=value.pw_name, pw_uid=value.pw_uid + 1,
                    pw_gid=value.pw_gid, pw_dir=value.pw_dir)
            return value
        monkeypatch.setattr(c.pwd, 'getpwnam', owner)
    with cold.bounded(60):
        if drift:
            with pytest.raises(c.CustodyError, match='owner-changed'):
                c.native_verify_installer_withdrawal(cold, n.receipt, transaction=n.transaction)
        else:
            assert c.native_verify_installer_withdrawal(cold, n.receipt, transaction=n.transaction) == n.receipt
    assert not any(hasattr(cold, name) for name in ('owner', 'home', '_runtime_config_path'))
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in before} == before


@pytest.mark.parametrize('home_drift', [False, True])
def test_actual_cold_constructor_absence_proof_uses_ds_home_without_runtime_selection(first_target, monkeypatch, home_drift):
    n = first_target
    journal = json.loads(n.upgrade.read_bytes())
    journal['phase'] = 'replacing-files'
    n.upgrade.write_bytes(c._native_encoded(journal))
    cold = cold_file_bridge(n, monkeypatch)
    before = {path: (path.read_bytes(), path.stat().st_ino) for path in n.metadata.iterdir() if path.is_file()}
    if home_drift:
        lookup = c.pwd.getpwnam
        def owner(name):
            value = lookup(name)
            if name == cold.gateway_owner:
                return SimpleNamespace(pw_name=value.pw_name, pw_uid=value.pw_uid,
                    pw_gid=value.pw_gid, pw_dir=str(n.world.parent))
            return value
        monkeypatch.setattr(c.pwd, 'getpwnam', owner)
    with cold.bounded(60):
        if home_drift:
            with pytest.raises(c.CustodyError, match='owner-changed'):
                c.native_verify_installer_absence(cold, n.absence, transaction=n.transaction)
        else:
            assert c.native_verify_installer_absence(cold, n.absence, transaction=n.transaction) == n.absence
    assert not any(hasattr(cold, name) for name in ('owner', 'home', '_runtime_config_path'))
    assert {path: (path.read_bytes(), path.stat().st_ino) for path in before} == before
