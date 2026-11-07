"""Real owner-file/Root-journal FDs; Linux/procfs metadata simulation only."""
import importlib.util
import json
import os
from pathlib import Path
import stat
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


@pytest.fixture
def inherited_sidefiles(sidefiles, monkeypatch):
    """Model Darwin's inherited group on real exclusively created child FDs."""
    n = sidefiles
    n.side_parent = n.side_parent.rename(n.side_parent.parent / '501')
    parent = n.side_parent.stat()
    n.owners[(parent.st_dev, parent.st_ino)] = (501, 20)
    n.plan['owner'] = SimpleNamespace(pw_uid=501, pw_gid=20)
    n.plan['runtime_bundle']['config_path'] = str(n.side_parent / 'openclaw.json')
    original = os.open
    def open_file(name, flags, *args, **kwargs):
        fd = original(name, flags, *args, **kwargs)
        if name in ('gateway.env', 'onboarding.json') and flags & os.O_EXCL and flags & os.O_CREAT:
            info = n.real_fstat(fd)
            n.owners[(info.st_dev, info.st_ino)] = (0, 20)
        return fd
    monkeypatch.setattr(os, 'open', open_file)
    return n


@pytest.mark.parametrize('interruption', [None, 'before-normalization', 'lost-normalization-ack'])
def test_inherited_group_is_normalized_only_after_inode_receipt(inherited_sidefiles, monkeypatch, interruption):
    n = inherited_sidefiles
    actual, calls = os.fchown, []
    def chown(fd, uid, gid):
        info = n.real_fstat(fd)
        name = Path(os.readlink('/proc/self/fd/' + str(fd))).name
        child = saved(n)['children'][name]
        assert child == {'inode': [info.st_dev, info.st_ino], 'ready': False}
        if (uid, gid) == (0, 0):
            assert info.st_size == 0
        calls.append((name, uid, gid))
        if interruption == 'before-normalization' and len(calls) == 1:
            raise OSError('inherited-group interrupted')
        actual(fd, uid, gid)
        if interruption == 'lost-normalization-ack' and len(calls) == 1:
            raise OSError('inherited-group interrupted')
    monkeypatch.setattr(os, 'fchown', chown)
    if interruption:
        with pytest.raises(OSError, match='inherited-group interrupted'):
            installer._connection_sidefiles(n.plan)
        first = saved(n)['children']['gateway.env']['inode']
        assert first is not None and (n.side_parent / 'gateway.env').read_bytes() == b''
    else:
        first = None
    installer._connection_sidefiles(n.plan)
    complete = saved(n)
    if first is not None: assert complete['children']['gateway.env']['inode'] == first
    assert all(item['ready'] for item in complete['children'].values())
    for name, text in n.plan['runtime_bundle']['connection_sidefiles'].items():
        with (n.side_parent / name).open('rb') as stream:
            info = os.fstat(stream.fileno())
            assert (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink) == (501, 20, 0o600, 1)
            assert stream.read() == text.encode()
    installer._connection_sidefiles(n.plan)
    assert saved(n) == complete


def test_unrecorded_empty_inherited_group_file_remains_unadoptable(inherited_sidefiles, monkeypatch):
    n = inherited_sidefiles
    actual = pixel_macos_custody._require_no_acl
    def interrupted(fd):
        actual(fd)
        if Path(os.readlink('/proc/self/fd/' + str(fd))).name == 'gateway.env':
            raise installer.InstallError('legacy-before-inode-interruption')
    monkeypatch.setattr(pixel_macos_custody, '_require_no_acl', interrupted)
    with pytest.raises(installer.InstallError, match='legacy-before-inode-interruption'):
        installer._connection_sidefiles(n.plan)
    leaf = n.side_parent / 'gateway.env'
    inode, receipt = leaf.stat().st_ino, (n.metadata / 'source-sidefiles.json').read_bytes()
    assert saved(n)['children']['gateway.env'] == {'inode': None, 'ready': False}
    assert leaf.read_bytes() == b''
    monkeypatch.setattr(pixel_macos_custody, '_require_no_acl', actual)
    with pytest.raises(FileExistsError): installer._connection_sidefiles(n.plan)
    assert leaf.stat().st_ino == inode and leaf.read_bytes() == b''
    assert (n.metadata / 'source-sidefiles.json').read_bytes() == receipt


@pytest.mark.parametrize('change', ['nonempty', 'other-group', 'new-inode', 'hardlink', 'mode', 'acl'])
def test_inherited_group_resume_never_normalizes_unknown_state(inherited_sidefiles, monkeypatch, change):
    n = inherited_sidefiles
    chown = os.fchown
    monkeypatch.setattr(os, 'fchown', lambda *_a: (_ for _ in ()).throw(OSError('before-normalization')))
    with pytest.raises(OSError, match='before-normalization'): installer._connection_sidefiles(n.plan)
    monkeypatch.setattr(os, 'fchown', chown)
    leaf = n.side_parent / 'gateway.env'
    if change == 'nonempty': leaf.write_bytes(b'P')
    elif change == 'other-group':
        info = leaf.stat()
        n.owners[(info.st_dev, info.st_ino)] = (0, 21)
    elif change == 'new-inode':
        replacement = n.side_parent / 'foreign'
        replacement.write_bytes(b'')
        replacement.chmod(0o600)
        info = replacement.stat()
        n.owners[(info.st_dev, info.st_ino)] = (0, 20)
        os.replace(replacement, leaf)
    elif change == 'hardlink': os.link(leaf, n.side_parent / 'alias')
    elif change == 'mode': leaf.chmod(0o640)
    else:
        original = pixel_macos_custody._require_no_acl
        def acl(fd):
            original(fd)
            if Path(os.readlink('/proc/self/fd/' + str(fd))).name == 'gateway.env':
                raise pixel_macos_custody.CustodyError('fixture-acl')
        monkeypatch.setattr(pixel_macos_custody, '_require_no_acl', acl)
    body, inode, receipt = leaf.read_bytes(), leaf.stat().st_ino, (n.metadata / 'source-sidefiles.json').read_bytes()
    calls = list(n.changes)
    with pytest.raises((installer.InstallError, pixel_macos_custody.CustodyError)):
        installer._connection_sidefiles(n.plan)
    assert n.changes == calls and leaf.read_bytes() == body and leaf.stat().st_ino == inode
    assert (n.metadata / 'source-sidefiles.json').read_bytes() == receipt


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
