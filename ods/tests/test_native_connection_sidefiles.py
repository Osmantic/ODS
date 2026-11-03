"""Real owner-file/Root-journal FDs; Linux/procfs metadata simulation only."""
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

if not sys.platform.startswith('linux') or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs private FD harness; not Darwin qualification',allow_module_level=True)
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'tests'),str(ROOT/'bin')]
from test_native_source_custody import native  # noqa: E402,F401
from test_connection_deployment_paths import deployment  # noqa: E402,F401
import pixel_macos_custody  # noqa: E402

spec=importlib.util.spec_from_file_location('source_sidefile_installer',ROOT/'installers/macos/lib/pixel-macos-access-install.py')
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


@pytest.fixture
def sidefiles(native,monkeypatch):  # noqa: F811
    n=native
    config_root=n.world/'config'
    parent=config_root/str(os.getuid())
    parent.mkdir(parents=True,mode=0o700)
    parent.chmod(0o700)
    info=parent.stat()
    n.owners[(info.st_dev,info.st_ino)]=(os.getuid(),os.getgid())
    monkeypatch.setattr(installer,'RUNTIME_CONFIG_ROOT',config_root)
    monkeypatch.setattr(installer._launchd,'ACCESS_STATE',n.metadata)
    def no_acl(fd):
        assert not any('acl' in name.casefold() for name in os.listxattr(fd))
    monkeypatch.setattr(pixel_macos_custody,'_require_no_acl',no_acl)
    n.side_parent=parent
    n.plan={'owner':SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()),'runtime_bundle':{
        'config_path':str(parent/'openclaw.json'),'digest':'b'*64,'connection_sidefiles':{
            'gateway.env':"PIXEL_LIMB_EMAIL_ENABLED='0'\n",'onboarding.json':'{"invented":true}\n'}}}
    return n


def saved(n):return json.loads((n.metadata/'source-sidefiles.json').read_bytes())


def test_explicit_owner_producer_records_inode_before_data_and_chown(sidefiles,monkeypatch):
    n=sidefiles
    original=os.fchown
    observed=[]
    def chown(fd,uid,gid):
        info=n.real_fstat(fd)
        record=saved(n)
        name=Path(os.readlink('/proc/self/fd/'+str(fd))).name
        assert record['children'][name]['inode']==[info.st_dev,info.st_ino]
        assert record['children'][name]['ready'] is False
        observed.append(name)
        return original(fd,uid,gid)
    monkeypatch.setattr(os,'fchown',chown)
    installer._connection_sidefiles(n.plan)
    assert set(observed)=={'gateway.env','onboarding.json'}
    first=saved(n)
    for name,text in n.plan['runtime_bundle']['connection_sidefiles'].items():
        assert (n.side_parent/name).read_bytes()==text.encode()
        with (n.side_parent/name).open('rb') as stream:
            info=os.fstat(stream.fileno())
            assert (info.st_uid,info.st_gid,info.st_mode&0o777)==(os.getuid(),os.getgid(),0o600)
    installer._connection_sidefiles(n.plan)
    assert saved(n)==first
    assert 'PIXEL_LIMB_EMAIL_ENABLED=' not in (n.metadata/'source-sidefiles.json').read_text()


@pytest.mark.parametrize('what',['prior-file','prior-symlink','parent-mode','changed-body','same-body-new-inode','hardlink','receipt-binding'])
def test_unknown_or_changed_owner_state_never_adopted_or_repaired(sidefiles,what):
    n=sidefiles
    leaf=n.side_parent/'gateway.env'
    if what=='prior-file':leaf.write_bytes(b'preserve foreign owner input')
    elif what=='prior-symlink':leaf.symlink_to(n.f.env)
    elif what=='parent-mode':n.side_parent.chmod(0o750)
    else:
        installer._connection_sidefiles(n.plan)
        if what=='changed-body':leaf.write_bytes(b'preserve changed body')
        elif what=='same-body-new-inode':
            before=leaf.stat().st_ino
            replacement=n.side_parent/'owner-replacement'
            replacement.write_bytes(leaf.read_bytes())
            replacement.chmod(0o600)
            info=replacement.stat()
            n.owners[(info.st_dev,info.st_ino)]=(os.getuid(),os.getgid())
            os.replace(replacement,leaf)
            assert leaf.stat().st_ino!=before
        elif what=='hardlink':os.link(leaf,n.side_parent/'second-link')
        else:
            value=saved(n)
            value['bundleSha256']='c'*64
            (n.metadata/'source-sidefiles.json').write_text(json.dumps(value))
    before={p.name:p.read_bytes() for p in n.side_parent.iterdir() if p.is_file() and not p.is_symlink()}
    with pytest.raises((installer.InstallError,OSError)):
        installer._connection_sidefiles(n.plan)
    assert {p.name:p.read_bytes() for p in n.side_parent.iterdir() if p.is_file() and not p.is_symlink()}==before


def test_interrupted_known_chown_prefix_resumes_same_inode_without_reset(sidefiles,monkeypatch):
    n=sidefiles
    original=os.fchown
    hit=[]
    def fail(fd,uid,gid):
        if not hit:
            hit.append(True)
            raise OSError('invented chown interruption')
        return original(fd,uid,gid)
    monkeypatch.setattr(os,'fchown',fail)
    with pytest.raises(OSError,match='chown interruption'):installer._connection_sidefiles(n.plan)
    before=saved(n)['children']['gateway.env']['inode']
    assert before is not None and saved(n)['children']['gateway.env']['ready'] is False
    monkeypatch.setattr(os,'fchown',original)
    installer._connection_sidefiles(n.plan)
    assert saved(n)['children']['gateway.env']['inode']==before
    assert all(item['ready'] for item in saved(n)['children'].values())


def test_source_recovery_family_is_absent_or_complete_never_arbitrary_subset():
    _required, optional = installer._recovery_file_contract()
    family = {path for path in optional if '/pixel_connections/' in path or path.endswith(
        (*installer.SOURCE_HOST_FILES, 'source-runtime-installation.json',
         'pixel-provider-runtime-manifest.json', 'pixel-provider-runtime.json'))}
    assert len(family) == len(installer.CONNECTION_FILES) + len(installer.SOURCE_HOST_FILES) + 3
    installer._require_complete_source_file_family(set())
    installer._require_complete_source_file_family(family)
    for missing in family:
        with pytest.raises(installer.InstallError, match='family-incomplete'):
            installer._require_complete_source_file_family(family - {missing})


def test_source_initial_preflight_refuses_unknown_operator_descriptor_and_noninitial(sidefiles, monkeypatch):
    n = sidefiles
    monkeypatch.setattr(installer, '_destination', lambda path: n.world / Path(path).relative_to('/'))
    descriptor = installer._destination(Path('/private/etc/ods/pixel-provider-runtime.json'))
    descriptor.parent.mkdir(parents=True)
    descriptor.write_bytes(b'preserve unknown operator descriptor')
    n.plan['initial_install'] = True
    with pytest.raises(installer.InstallError, match='operator-runtime-preserved'):
        installer._preflight_source_publication(n.plan)
    assert descriptor.read_bytes() == b'preserve unknown operator descriptor'
    n.plan['initial_install'] = False
    with pytest.raises(installer.InstallError, match='initial-publication-required'):
        installer._preflight_source_publication(n.plan)
    assert descriptor.read_bytes() == b'preserve unknown operator descriptor'
