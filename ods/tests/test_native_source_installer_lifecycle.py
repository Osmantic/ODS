"""Installer custody fixtures; no service, account, provider or native host is used."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('source_lifecycle_native_install',
    ROOT / 'installers/macos/lib/pixel-native-install.py')
install = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install)


def test_joint_native_migration_cannot_bypass_source_admission_before_root_reads(monkeypatch):
    spec = importlib.util.spec_from_file_location('source_lifecycle_upgrade_entry',
        ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
    access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(access)
    monkeypatch.setattr(access.sys, 'platform', 'darwin')
    monkeypatch.setattr(access.os, 'geteuid', lambda: 0)
    plan = {'upgrade_qualification': {'currentDigest': 'a' * 64},
        'runtime_bundle': {'digest': 'b' * 64, 'connection_sidefiles':
            {'gateway.env': 'explicit candidate', 'onboarding.json': '{}'}}}
    with pytest.raises(access.InstallError, match='native-source-upgrade-not-qualified'):
        access._execute_upgrade_install(plan, '/must-not-be-opened')


@pytest.mark.parametrize('residue', [
    '/private/var/lib/pixel-source-broker',
    '/private/var/log/pixel-source-broker',
    '/private/etc/ods/pixel-provider-runtime.json',
    '/private/etc/ods/pixel-provider-runtime-manifest.json',
])
def test_initial_install_preserves_source_residue_before_tools_or_identity_checks(tmp_path, monkeypatch, residue):
    """An initial install cannot reinterpret old Source state as a clean identity."""
    assert Path(residue) in install.NATIVE_RESIDUE_PATHS
    retained = tmp_path / 'retained'
    retained.mkdir()
    token = retained / 'invented-token.json'
    token.write_bytes(b'INVENTED_TEST_TOKEN_MUST_REMAIN')
    before = token.stat()
    original = install.os.path.lexists
    monkeypatch.setattr(install.sys, 'platform', 'darwin')
    monkeypatch.setattr(install.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(install.os, 'geteuid', lambda: 501)
    monkeypatch.setattr(install.os.path, 'lexists',
        lambda path: original(retained) if str(path) == residue else False)
    monkeypatch.setattr(install, 'retained_identity_only',
        lambda **_kw: pytest.fail('residue must refuse before identity adoption'))
    monkeypatch.setattr(install, 'command',
        lambda *_a, **_kw: pytest.fail('initial preflight must not run tools'))
    with pytest.raises(ValueError, match='existing-native-pixel-requires-migration-or-recovery'):
        install.preflight(tmp_path / 'ods')
    assert (token.stat().st_dev, token.stat().st_ino) == (before.st_dev, before.st_ino)
    assert token.read_bytes() == b'INVENTED_TEST_TOKEN_MUST_REMAIN'
    assert set(retained.iterdir()) == {token}


def test_initial_deployment_preserves_static_publisher_inode_transaction(monkeypatch):
    """Prewriting metadata would make the genuine initial publisher refuse it."""
    spec = importlib.util.spec_from_file_location('initial_source_metadata_order',
        ROOT / 'installers/macos/lib/pixel-macos-access-install.py')
    access = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(access)
    plan = dict(runtime_bundle={'connection_sidefiles': {'gateway.env': 'candidate', 'onboarding.json': '{}'}},
        policies={'sandboxed': b'sandbox fixture'}, launcher=b'launcher fixture', key=b'fixture key',
        owner=SimpleNamespace(pw_uid=501, pw_gid=20), gateway={}, access={},
        access_settings={'gateway_policy': {'profiles': {}}})
    monkeypatch.setattr(access, '_source_flat_files', lambda _s:
        [('pixel_connections/custody.py', b'explicit candidate flat code')])
    monkeypatch.setattr(access, '_source_family_files', lambda _p:
        pytest.fail('initial metadata must retain its own publisher journal, not the upgrade renderer'))
    files = access._deployment_files(ROOT, plan)
    paths = {str(p) for p, _b, _a in files}
    assert str(access.ACCESS_PROGRAM_ROOT / 'pixel_connections/custody.py') in paths
    assert not paths.intersection({'/private/etc/ods/pixel-provider-runtime.json',
        '/private/etc/ods/pixel-provider-runtime-manifest.json',
        str(access._launchd.ACCESS_STATE / 'source-runtime-installation.json')})
