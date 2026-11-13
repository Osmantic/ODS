"""Actual initial-generation allocation, receipts and exclusive preservation.

The existing Linux FD/Directory Service driver supplies Darwin observations;
no installed Mac files, actors, Google data or provider process are used.
"""
# ruff: noqa: E402,F401,F811
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sys
import time

import pytest

if sys.platform != 'linux' or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux Darwin metadata driver, not physical Mac', allow_module_level=True)
sys.path.insert(0,str(Path(__file__).resolve().parent))
from test_native_source_empty_gate_preservation import (last_child, gate, first_target, material, native,
    deployment, built, source, package, runtime_custody, bundle, services, helper, qualification_operation,
    old_component_owner_observation, p)
from test_native_source_initial_backend import fresh_file_metadata
from pixel_connections import custody as c, native_executor as ex
import pixel_access_bridge as access
import pixel_macos_custody as mac

pytestmark = pytest.mark.parametrize('first_target', ['code-only'], indirect=True)


@pytest.fixture
def manifest(last_child, monkeypatch):
    n = last_child
    fresh_file_metadata(n,monkeypatch)
    p.preserve_empty_child(c,mac,n.selected,n.expected)
    c.native_prepare_state(n.selected,n.identity)
    c.native_publish_environment(n.selected,c.native_environment_text(n.selected,preauth=True))
    c.native_prepare_logs(n.selected)
    units = n.world/'initial-units'
    units.mkdir(mode=0o755)
    monkeypatch.setattr(c,'NATIVE_DEFINITIONS',units)
    previous = mac.protected_directory
    @contextmanager
    def directory(path,**kwargs):
        if Path(path) == units:
            assert not kwargs
            fd = os.open(path,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: yield fd
            finally: os.close(fd)
        else:
            with previous(path,**kwargs) as fd: yield fd
    monkeypatch.setattr(mac,'protected_directory',directory)
    monkeypatch.setattr(ex,'protected_directory',directory)
    n.boot = '01234567-89AB-4DEF-8123-0123456789AB'
    command = n.f.bridge.command
    def commands(argv,timeout=20):
        if argv == ['/usr/sbin/sysctl','-n','kern.bootsessionuuid']:
            return n.boot+'\n'
        return command(argv,timeout=timeout)
    monkeypatch.setattr(n.f.bridge,'command',commands)
    monkeypatch.setattr(c,'_native_boot_uuid',lambda:n.boot)
    clock = lambda:time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(c,'_native_clock_ns',clock)
    monkeypatch.setattr(access,'_native_source_clock_ns',clock)
    original = c._native_save_at
    def interrupted(selection,directory,name,value,**kwargs):
        if name == 'source-jobs-prepare.json' and value['manifest']['inode'] is not None:
            raise OSError('manifest-allocation-before-receipt')
        return original(selection,directory,name,value,**kwargs)
    with monkeypatch.context() as fault:
        fault.setattr(c,'_native_save_at',interrupted)
        with pytest.raises(OSError,match='manifest-allocation-before-receipt'):
            helper.publish_initial_generation(n.selected)
    n.manifest_path = n.metadata/'native-source-jobs/manifest.json'
    st = n.manifest_path.stat()
    n.manifest_pair = [st.st_dev,st.st_ino]
    assert st.st_size == 0
    child = json.loads((n.metadata/'source-jobs-prepare.json').read_bytes())
    assert child['manifest'] == {'inode':None,'ready':False}
    assert len(child['definitions']) == sum(item['ready'] for item in child['definitions'].values()) == 2
    n.images = {leaf:((n.metadata/leaf).read_bytes(),(n.metadata/leaf).stat().st_ino)
        for leaf in p.MANIFEST_COPIES.values()}
    n.units = units
    return n


def preserve(n):
    return p.preserve_empty_manifest(c,mac,n.selected,n.expected,n.manifest_pair)


def unchanged(n):
    assert {leaf:((n.metadata/leaf).read_bytes(),(n.metadata/leaf).stat().st_ino)
        for leaf in p.MANIFEST_COPIES.values()} == n.images


def test_unrecorded_manifest_is_retained_then_original_publisher_finishes(manifest):
    n = manifest
    with pytest.raises(ValueError,match='generation-prefix-changed'):
        helper.publish_initial_generation(n.selected)
    result = preserve(n)
    archived = Path(result['archive'])/'unrecorded-manifest'
    assert result['status'] == 'preserved-empty-first-manifest' and not result['sourcePrepared']
    assert archived.stat().st_ino == n.manifest_pair[1] and not archived.read_bytes()
    assert not n.manifest_path.exists()
    unchanged(n)
    assert preserve(n) == result
    unchanged(n)
    assert helper.publish_initial_generation(n.selected)['status'] == 'inactive'
    assert n.manifest_path.stat().st_ino != n.manifest_pair[1]
    assert archived.stat().st_ino == n.manifest_pair[1]
    assert all(argv[:2] not in (['/bin/launchctl','bootstrap'],['/bin/launchctl','kickstart']) for argv in n.commands)


@pytest.mark.parametrize('change',['content','inode','acl','recorded','unknown','token','definition','prior-receipt',
    'hardlink','mode','owner'])
def test_manifest_preservation_refuses_changes(manifest,change):
    n = manifest
    if change == 'content': n.manifest_path.write_bytes(b'keep')
    elif change == 'inode': n.manifest_pair[1] += 1
    elif change == 'acl':
        n.acl.acls[tuple(n.manifest_pair)] = {'entries':[{'tag':1,'uuid':b'x'*16,'mask':1}],'flags':0}
    elif change == 'recorded':
        path = n.metadata/'source-jobs-prepare.json'
        v=json.loads(path.read_bytes())
        v['manifest']['inode']=n.manifest_pair
        path.write_bytes(c._native_encoded(v))
    elif change == 'unknown': (n.manifest_path.parent/'unknown').write_bytes(b'keep')
    elif change == 'token': (c.NATIVE_STATE/'private/google-token.json').write_bytes(b'keep')
    elif change == 'definition':
        next(n.manifest_path.parent.glob('*.plist')).write_bytes(b'keep')
    elif change == 'hardlink': os.link(n.manifest_path,n.world/'retained-link')
    elif change == 'mode': n.manifest_path.chmod(0o644)
    elif change == 'owner': n.owners[tuple(n.manifest_pair)]=(60001,60001)
    else: n.expected['images']['code.json']['sha256']='0'*64
    before=set(p.ARCHIVES.iterdir())
    with pytest.raises((p.PrefixError,c.CustodyError,ValueError,OSError)):
        preserve(n)
    assert set(p.ARCHIVES.iterdir()) == before and n.manifest_path.exists()


@pytest.mark.parametrize('when',['before','after'])
def test_manifest_lost_rename_ack_preserves_same_inodes(manifest,monkeypatch,when):
    n = manifest
    rename = p.rename_exclusive
    def fault(*args):
        if when == 'after': rename(*args)
        raise OSError('manifest-lost-ack')
    with monkeypatch.context() as injected:
        injected.setattr(p,'rename_exclusive',fault)
        with pytest.raises(OSError,match='manifest-lost-ack'):preserve(n)
    result = preserve(n)
    assert (Path(result['archive'])/'unrecorded-manifest').stat().st_ino == n.manifest_pair[1]
    unchanged(n)


def test_unsealed_manifest_archive_is_retained_and_not_recreated(manifest,monkeypatch):
    n = manifest
    original = p.write_new
    def fault(custody,directory,name,body):
        if name == 'jobs.json': raise OSError('interrupted-manifest-copy')
        return original(custody,directory,name,body)
    with monkeypatch.context() as injected:
        injected.setattr(p,'write_new',fault)
        with pytest.raises(OSError,match='interrupted-manifest-copy'):preserve(n)
    before={path:(path.read_bytes(),path.stat().st_ino) for path in p.ARCHIVES.rglob('*') if path.is_file()}
    with pytest.raises(p.PrefixError,match='unsealed-or-unknown'):preserve(n)
    assert {path:(path.read_bytes(),path.stat().st_ino) for path in before} == before
    assert n.manifest_path.stat().st_ino == n.manifest_pair[1]
    unchanged(n)
