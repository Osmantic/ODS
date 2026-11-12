"""Actual Root publication FDs/renames; explicit Linux/procfs metadata harness.

Uses the actual bridge/RuntimeCustody/bundle qualification. Only Darwin
UID/GID/ACL/DS/loaded-process metadata and read-only OS observations are simulated.
No account, job, SDK, model or Google request is executed.
"""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import sys
from contextlib import contextmanager

import pytest

if not sys.platform.startswith('linux') or not Path('/proc/self/fd').is_dir():
    pytest.skip('Linux/procfs publication harness, not physical Darwin qualification', allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'tests'),str(ROOT/'bin')]
from test_native_source_custody import native, launchctl_system  # noqa: E402,F401
from test_connection_deployment_paths import deployment  # noqa: E402,F401
from test_native_source_runtime_publication import built, source, package, helper  # noqa: E402,F401
from pixel_provider import runtime_custody  # noqa: E402
from pixel_connections import custody  # noqa: E402
import pixel_macos_custody  # noqa: E402


@pytest.fixture
def material(native, built, monkeypatch):  # noqa: F811
    n, f = native, native.f
    root = f.root.parent / built.digest
    built.root.rename(root)
    descriptor_path = runtime_custody.DESCRIPTOR
    descriptor = json.loads(descriptor_path.read_bytes())
    descriptor.update(runtimeRoot=str(root/'runtime'),sourceRoot=str(root/'ods-source'),
        node=str(root/'node'),hostPython=str(built.python))
    launcher = Path(descriptor['launcher'])
    launcher.write_bytes(('#!/bin/sh\nexec /usr/bin/env -u NODE_OPTIONS -u NODE_PATH '+str(root/'node')
        +' '+str(root/'runtime/openclaw.mjs')+' "$@"\n').encode())
    launcher.chmod(0o755)
    manifest = {'runtime':runtime_custody.tree_manifest(root/'runtime'),
        'source':runtime_custody.tree_manifest(root/'ods-source'),'node':runtime_custody._regular(root/'node'),
        'launcher':runtime_custody._regular(launcher),'hostPython':runtime_custody._regular(built.python)}
    body = helper.encoded(manifest)
    Path(descriptor['manifest']).write_bytes(body)
    descriptor['manifestSha256'] = hashlib.sha256(body).hexdigest()
    descriptor_path.write_bytes(helper.encoded(descriptor))
    document = plistlib.loads(f.plist.read_bytes())
    args = document['ProgramArguments']
    args[-2:] = [str(root/'node'),str(root/'runtime/openclaw.mjs')]
    args[:] = [item if not item.startswith('PATH=') else 'PATH='+str(root)+':/usr/bin:/bin' for item in args]
    f.plist.write_bytes(plistlib.dumps(document))
    f.bridge.gateway_process['executable'] = str(root/'node')
    f.root = root
    f.value = f.bridge.connection_deployment()
    n.selected = custody.native_source_selection(f.bridge,f.value)
    # Only the relocated protected import location is modeled. The code bytes
    # and every immutable provenance/component proof remain the actual ones.
    copied_helper = root / 'ods-source/installers/macos/lib/pixel-native-source-broker.py'
    assert copied_helper.read_bytes() == Path(helper.__file__).read_bytes()
    monkeypatch.setattr(helper, '__file__', str(copied_helper))
    # Read-only OS metadata observations. Every other command is an error.
    n.commands=[]
    def command(argv,timeout=20):
        n.commands.append(list(argv))
        if argv==['/bin/launchctl','print','system']:return launchctl_system()
        if argv==['/bin/ps','-axo','pid=,uid=']:return '1 0\n42 1000\n'
        if argv==['/usr/sbin/sysctl','-n','kern.bootsessionuuid']:return '01234567-89AB-4DEF-8123-0123456789AB\n'
        raise AssertionError('No launch/mutation/unbound command: '+repr(argv))
    monkeypatch.setattr(f.bridge,'command',command)
    # Bundle ownership is simulated as Root independently of the real inode,
    # bounded/no-follow read and digest verification, which remain actual.
    original_read=helper._read_at
    def read(directory,name,**kwargs):
        location=Path(os.readlink('/proc/self/fd/'+str(directory)))
        if location.is_relative_to(root):kwargs.update(uid=os.getuid(),gid=os.getgid())
        return original_read(directory,name,**kwargs)
    monkeypatch.setattr(helper,'_read_at',read)
    def no_acl(fd):
        assert not any('acl' in name.casefold() for name in os.listxattr(fd))
    monkeypatch.setattr(pixel_macos_custody,'_require_no_acl',no_acl)
    files=helper.bundle_payloads(root,ref=built.package[1],python=str(built.python),protected=True)
    for name in ('broker.py','source-broker.env'):
        (n.kit/name).write_bytes(files[name])
        (n.kit/name).chmod(0o755 if name=='broker.py' else 0o644)
    old={'schemaVersion':1,'kind':'ods-native-source-kit','deploymentSha256':f.value.checksum,
        'sourceRef':built.package[1],'files':{name:helper.sha(files[name]) for name in ('broker.py','source-broker.env')}}
    (n.kit/'source-kit.json').write_bytes(helper.encoded(old))
    one=n.acl.acl_init
    monkeypatch.setattr(n.acl,'acl_init',lambda count:n.acl.put({'entries':[],'flags':0}) if count==1 else one(count))
    monkeypatch.setattr(custody,'NATIVE_LOGS',n.world/'logs')
    return n


@pytest.fixture
def publication(material):
    n=material
    custody.native_prepare_state(n.selected,n.identity)
    custody.native_publish_environment(n.selected,custody.native_environment_text(n.selected,preauth=True))
    custody.native_prepare_logs(n.selected)
    n.old_files={name:(n.kit/name).read_bytes() for name in os.listdir(n.kit)}
    n.old_metadata={name:(n.metadata/name).read_bytes() for name in ('source-custody-prepare.json','source-environment.json','source-logs-prepare.json')}
    n.old_metadata['custody.json']=(custody.NATIVE_STATE/'custody.json').read_bytes()
    n.env_before=custody.NATIVE_ENV.read_bytes()
    n.env_inode=custody.NATIVE_ENV.stat().st_ino
    return n


def record(n):
    return json.loads((n.metadata/'source-publication.json').read_bytes())


def test_same_deployment_a_to_b_uses_real_pub_and_keeps_data_and_policies(publication):
    n=publication
    old=(n.kit.stat().st_dev,n.kit.stat().st_ino)
    result=helper.publish_kit(n.selected)
    assert result['status']=='prepared-no-jobs'
    assert custody.native_verify_kit(n.selected,require_profiles=True)[1]==result['kitSha256']
    saved=record(n)
    assert saved['old']['inode']==list(old) and saved['phase']=='prepared-no-jobs'
    assert len(saved['new'])==9 and len(saved['bindings'])==4
    assert all(custody.NATIVE_ENV.read_bytes()==n.env_before for _ in range(2))
    assert custody.NATIVE_ENV.stat().st_ino==n.env_inode
    assert helper.publish_kit(n.selected)==result
    before_readback=(n.metadata/'source-publication.json').read_bytes()
    assert helper.verify_kit_publication(n.selected)==result
    assert (n.metadata/'source-publication.json').read_bytes()==before_readback
    assert all(argv[1] not in ('bootstrap','kickstart','enable','bootout') for argv in n.commands)


def test_rollback_restores_exact_old_kit_metadata_inodes_and_no_token_snapshot(publication):
    n=publication
    helper.publish_kit(n.selected)
    before=record(n)
    result=helper.publish_kit(n.selected,rollback=True)
    assert result['status']=='restored'
    assert set(os.listdir(n.kit))==set(n.old_files)
    assert {name:(n.kit/name).read_bytes() for name in n.old_files}==n.old_files
    for name,body in n.old_metadata.items():
        path=custody.NATIVE_STATE/name if name=='custody.json' else n.metadata/name
        assert path.read_bytes()==body
        assert path.stat().st_ino==before['bindings'][name]['beforeInode'][1]
    assert custody.native_verify_kit(n.selected)[0]['schemaVersion']==1
    assert custody.NATIVE_ENV.read_bytes()==n.env_before
    assert 'refresh_token' not in json.dumps(record(n))


@pytest.mark.parametrize('when',['before-old-rename','after-old-rename','after-new-rename'])
def test_kit_rename_lost_reply_resumes_exact_inodes_no_second_effect(publication,monkeypatch,when):
    n=publication
    original=os.rename
    hit=[]
    def rename(first,second,**kwargs):
        old=first==n.kit.name and str(second).startswith('.source-kit-before-')
        new=str(first).startswith('.source-kit-') and second==n.kit.name
        if not hit and ((when=='before-old-rename' and old) or (when=='after-old-rename' and old) or (when=='after-new-rename' and new)):
            hit.append(True)
            if when!='before-old-rename':original(first,second,**kwargs)
            raise OSError('invented lost rename reply')
        return original(first,second,**kwargs)
    monkeypatch.setattr(os,'rename',rename)
    with pytest.raises(OSError,match='lost rename'):helper.publish_kit(n.selected)
    assert hit
    saved=record(n)
    with pytest.raises(custody.CustodyError,match='publication-pending'):custody.native_environment_text(n.selected)
    monkeypatch.setattr(os,'rename',original)
    result=helper.publish_kit(n.selected)
    assert result['status']=='prepared-no-jobs' and record(n)['candidateInode']==saved['candidateInode']


@pytest.mark.parametrize('when',['before-binding-rename','after-binding-old-move','after-binding-promotion'])
def test_binding_rename_faults_restore_or_resume_from_exact_old_new(publication,monkeypatch,when):
    n=publication
    original=os.rename
    hit=[]
    def rename(first,second,**kwargs):
        old=str(second).startswith('.source-binding-before-')
        new=str(first).startswith('.source-binding-') and not str(first).startswith('.source-binding-before-')
        if not hit and ((when=='before-binding-rename' and old) or (when=='after-binding-old-move' and old) or (when=='after-binding-promotion' and new)):
            hit.append(True)
            if when!='before-binding-rename':original(first,second,**kwargs)
            raise OSError('invented binding interruption')
        return original(first,second,**kwargs)
    monkeypatch.setattr(os,'rename',rename)
    with pytest.raises(OSError,match='binding interruption'):helper.publish_kit(n.selected)
    assert hit
    monkeypatch.setattr(os,'rename',original)
    assert helper.publish_kit(n.selected)['status']=='prepared-no-jobs'
    assert helper.publish_kit(n.selected,rollback=True)['status']=='restored'


@pytest.mark.parametrize('what',['job','process','source-change','extra-kit','env-drift','private-template','wrong-domain'])
def test_unknown_existing_authority_refuses_before_publication_intent(publication,monkeypatch,what):
    n=publication
    if what in ('job','process','wrong-domain'):
        original=n.f.bridge.command
        def command(argv,timeout=20):
            if what=='job' and argv==['/bin/launchctl','print','system']:return launchctl_system('com.ods.pixel-native-source-sync')
            if what=='wrong-domain' and argv==['/bin/launchctl','print','system']:return 'gui/1000 = {\n}\n'
            if what=='process' and argv==['/bin/ps','-axo','pid=,uid=']:return '11 60001\n'
            return original(argv,timeout=timeout)
        monkeypatch.setattr(n.f.bridge,'command',command)
    elif what=='source-change':
        path=n.kit/'source-kit.json'
        value=json.loads(path.read_bytes())
        value['sourceRef']='0'*40
        path.write_bytes(helper.encoded(value))
    elif what=='extra-kit':(n.kit/'foreign').write_text('preserve')
    elif what=='private-template':
        leaf=n.kit/'source-broker.env'
        body=leaf.read_bytes().replace(b"PIXEL_SOURCE_GMAIL_QUERY='in:inbox'",b"PIXEL_SOURCE_GMAIL_QUERY='from:invented-private@example.invalid'")
        leaf.write_bytes(body)
        path=n.kit/'source-kit.json'
        value=json.loads(path.read_bytes())
        value['files']['source-broker.env']=helper.sha(body)
        path.write_bytes(helper.encoded(value))
    else:custody.NATIVE_ENV.write_bytes(b'preserve unbound changed env')
    with pytest.raises((ValueError,custody.CustodyError)):
        helper.publish_kit(n.selected)
    assert not (n.metadata/'source-publication.json').exists()


def test_publication_file_plan_rejects_private_template_without_normalization():
    component={'broker.py':b'code','source-broker.env':b"QUERY='default'\n",
        **{name:b'profile' for name in helper.PROFILES}}
    retained={'broker.py':b'code','source-broker.env':b"QUERY='invented-private'\n"}
    with pytest.raises(ValueError,match='private-template-migration-required'):
        helper._publication_files(component,'a'*40,'b'*64,retained)
    assert retained['source-broker.env']==b"QUERY='invented-private'\n"


def test_initial_publication_and_rollback_restore_exact_prior_absence(material):
    n=material
    for name in ('source-kit.json','broker.py','source-broker.env'):
        (n.kit/name).unlink()
    n.kit.rmdir()
    (n.metadata/'source-identity.json').unlink()
    result=helper.publish_kit(n.selected)
    assert result['status']=='prepared-no-jobs'
    assert not custody.NATIVE_STATE.exists() and not custody.NATIVE_LOGS.exists()
    assert record(n)['old'] is None and record(n)['bindings']=={}
    assert helper.publish_kit(n.selected,rollback=True)=={'status':'restored','kitSha256':None}
    assert not n.kit.exists() and not custody.NATIVE_STATE.exists()


@pytest.fixture
def runtime_publication(material, monkeypatch):
    n = material
    public = n.world / 'public-metadata'
    public.mkdir(mode=0o755)
    descriptor = runtime_custody.DESCRIPTOR
    launcher = Path(json.loads(descriptor.read_bytes())['launcher'])
    monkeypatch.setattr(helper, 'RUNTIME_ROOT', n.f.root.parent)
    monkeypatch.setattr(helper, 'PROGRAM_ROOT', launcher.parent)
    monkeypatch.setattr(helper, 'ROOT_META', n.metadata)
    monkeypatch.setattr(helper, 'DESCRIPTOR', public / 'pixel-provider-runtime.json')
    monkeypatch.setattr(helper, 'RUNTIME_MANIFEST', public / 'pixel-provider-runtime-manifest.json')
    original = pixel_macos_custody.protected_directory
    @contextmanager
    def directory(path, **kwargs):
        if Path(path) == public:
            assert not kwargs
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                info = os.fstat(fd)
                assert (info.st_uid, info.st_gid, info.st_mode & 0o777) == (0, 0, 0o755)
                yield fd
            finally:
                os.close(fd)
        else:
            with original(path, **kwargs) as fd:
                yield fd
    monkeypatch.setattr(pixel_macos_custody, 'protected_directory', directory)
    # Only Darwin/root metadata is simulated. Production bundle verification,
    # inode journaling, exclusive staging, file bodies and renames remain real.
    monkeypatch.setattr(sys, 'platform', 'darwin')
    monkeypatch.setattr(os, 'geteuid', lambda: 0)
    n.runtime_installation = {'source': str(ROOT), 'owner': os.getuid(), 'operation': 'initial-install'}
    (n.metadata / 'installation.json').write_bytes(helper.encoded({
        'schemaVersion': 1, 'phase': 'staging', **n.runtime_installation}))
    (n.metadata / 'installation.json').chmod(0o600)
    manifest, _ = helper._material(n.selected)[0].native_verify_kit(n.selected, _publication=True)
    assert manifest['sourceRef']
    n.runtime_args = (n.f.root, n.f.root.name, launcher.read_bytes(), json.loads(
        descriptor.read_bytes())['hostPython'])
    return n


def publish_runtime(n, **kwargs):
    return helper.publish_runtime_metadata(*n.runtime_args, installation=n.runtime_installation, **kwargs)


def test_runtime_metadata_descriptor_last_idempotent_and_absence_rollback(runtime_publication, monkeypatch):
    n = runtime_publication
    original = os.rename
    promoted = []
    def rename(first, second, **kwargs):
        if str(first).startswith('.source-runtime-'):
            value = json.loads((n.metadata / 'source-runtime-publication.json').read_bytes())
            assert value['phase'] == 'publishing'
            assert all(child['ready'] for child in value['children'].values())
            promoted.append(second)
        return original(first, second, **kwargs)
    monkeypatch.setattr(os, 'rename', rename)
    assert publish_runtime(n) == 'published-no-jobs'
    assert promoted == [helper.RUNTIME_MANIFEST.name, 'source-runtime-installation.json', helper.DESCRIPTOR.name]
    value = json.loads((n.metadata / 'source-runtime-publication.json').read_bytes())
    before = {path: list(item['inode']) for path, item in value['children'].items()}
    assert publish_runtime(n) == 'published-no-jobs'
    assert {path: [Path(path).stat().st_dev, Path(path).stat().st_ino] for path in before} == before
    assert publish_runtime(n, rollback=True) == 'restored'
    assert all(not Path(path).exists() for path in before)
    assert publish_runtime(n, rollback=True) == 'restored'
    with pytest.raises(ValueError, match='restored-intent-retained'):
        publish_runtime(n)
    assert not n.commands  # No actor, account or provider command.


def test_final_initial_runtime_readback_never_replays_publication(runtime_publication):
    n = runtime_publication
    assert publish_runtime(n) == 'published-no-jobs'
    path = n.metadata / 'installation.json'
    value = json.loads(path.read_bytes())
    value['phase'] = 'active'
    path.write_bytes(helper.encoded(value))
    intent = n.metadata / 'source-runtime-publication.json'
    before = intent.read_bytes()
    assert publish_runtime(n, verify=True) == 'published-no-jobs'
    assert intent.read_bytes() == before
    with pytest.raises(ValueError, match='installation-changed'):
        publish_runtime(n)
    assert intent.read_bytes() == before
    value = json.loads(before)
    value['phase'] = 'publishing'
    intent.write_bytes(helper.encoded(value))
    incomplete = intent.read_bytes()
    with pytest.raises(ValueError, match='publication-incomplete'):
        publish_runtime(n, verify=True)
    assert intent.read_bytes() == incomplete


@pytest.mark.parametrize('when', ['before-manifest', 'after-manifest', 'after-descriptor', 'during-chmod'])
def test_runtime_metadata_interrupted_prefix_resumes_exact_recorded_inode(runtime_publication, monkeypatch, when):
    n = runtime_publication
    original_rename, original_chmod = os.rename, os.fchmod
    hit = []
    def rename(first, second, **kwargs):
        if not hit and str(first).startswith('.source-runtime-') and (
                when in ('before-manifest', 'after-manifest') and second == helper.RUNTIME_MANIFEST.name
                or when == 'after-descriptor' and second == helper.DESCRIPTOR.name):
            hit.append(True)
            if when != 'before-manifest':
                original_rename(first, second, **kwargs)
            raise OSError('invented runtime rename lost reply')
        return original_rename(first, second, **kwargs)
    def chmod(fd, mode):
        if when == 'during-chmod' and not hit:
            path = Path(os.readlink('/proc/self/fd/' + str(fd)))
            if path.name.startswith('.source-runtime-'):
                hit.append(True)
                raise OSError('invented runtime chmod interruption')
        return original_chmod(fd, mode)
    monkeypatch.setattr(os, 'rename', rename)
    monkeypatch.setattr(os, 'fchmod', chmod)
    with pytest.raises(OSError, match='invented runtime'):
        publish_runtime(n)
    assert hit
    value = json.loads((n.metadata / 'source-runtime-publication.json').read_bytes())
    inodes = {path: child['inode'] for path, child in value['children'].items() if child['inode'] is not None}
    monkeypatch.setattr(os, 'rename', original_rename)
    monkeypatch.setattr(os, 'fchmod', original_chmod)
    assert publish_runtime(n) == 'published-no-jobs'
    assert all([Path(path).stat().st_dev, Path(path).stat().st_ino] == inode for path, inode in inodes.items())
    assert not n.commands


@pytest.mark.parametrize('what', ['unknown-descriptor', 'different-installation', 'active-installation', 'changed-body', 'different-inode'])
def test_runtime_metadata_unknown_or_active_state_is_preserved(runtime_publication, what):
    n = runtime_publication
    if what == 'unknown-descriptor':
        helper.DESCRIPTOR.write_bytes(b'preserve unknown operator descriptor')
        helper.DESCRIPTOR.chmod(0o644)
    elif what in ('different-installation', 'active-installation'):
        path = n.metadata / 'installation.json'
        value = json.loads(path.read_bytes())
        value['phase' if what == 'active-installation' else 'owner'] = 'active' if what == 'active-installation' else 99999
        path.write_bytes(helper.encoded(value))
    else:
        assert publish_runtime(n) == 'published-no-jobs'
        if what == 'changed-body':
            helper.DESCRIPTOR.write_bytes(b'preserve owner-unexpected body')
        else:
            replacement = helper.DESCRIPTOR.with_name('replacement')
            replacement.write_bytes(helper.DESCRIPTOR.read_bytes())
            replacement.chmod(0o644)
            os.replace(replacement, helper.DESCRIPTOR)
    before = helper.DESCRIPTOR.read_bytes() if helper.DESCRIPTOR.exists() else None
    with pytest.raises(ValueError):
        publish_runtime(n)
    assert (helper.DESCRIPTOR.read_bytes() if helper.DESCRIPTOR.exists() else None) == before
