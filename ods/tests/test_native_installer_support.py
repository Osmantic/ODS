"""Actual Root files and isolated Python; Darwin ACL/platform is an OS driver."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

SOURCE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('support_qa', SOURCE / 'bin/pixel_native_installer_entry.py')
s = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(s)

pytestmark = pytest.mark.skipif(sys.platform != 'linux' or os.geteuid() != 0,
    reason='real Root-owned Linux file qualification, not physical Darwin')


@pytest.fixture
def support(tmp_path, monkeypatch):
    assert str(tmp_path).startswith('/root/'), 'Root QA must stay in its explicit private namespace'
    source = tmp_path / 'source'
    source.mkdir(mode=0o755)
    for target, origin in s._sources(SOURCE).items():
        path = source / origin
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / origin, path)
    monkeypatch.setattr(s, 'SUPPORT_ROOT', tmp_path / 'support-parent/support')
    monkeypatch.setattr(s.sys, 'platform', 'darwin')
    monkeypatch.setattr(s, '_acl', lambda fd: os.fstat(fd))  # Apple ACL syscall only.
    return source, Path(sys.executable).resolve()


def test_literal_closure_covers_all_mac_and_installer_python_peers(support):
    assert set(s.MAC_LIB) == {p.name for p in (SOURCE / 'installers/macos/lib').glob('*.py')}
    assert set(s.INSTALLER_LIB) == {p.name for p in (SOURCE / 'installers/lib').glob('*.py')}
    assert set(s.HOST_CODE) == {p.name for p in (SOURCE / 'extensions/services/pixel-agent/host').iterdir()
        if p.suffix in ('.py', '.mjs', '.sh')}
    mapping = s._sources(support[0])
    assert len(mapping) > 200 and all(Path(name).suffix in ('.py', '.mjs', '.sh')
        or name == 'bin/ods-pixel-route-lease' for name in mapping)


def test_real_root_publication_is_closed_and_idempotent(support):
    source, python = support
    result = s.stage_support(source, python=python)
    root = Path(result['root'])
    manifest = s._verify(root)
    assert result == {'root': str(root), 'entry': str(root / s.ENTRY), 'codeSha256': root.name, 'python': str(python)}
    before = {p.relative_to(root).as_posix(): (p.stat().st_ino, p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    assert s.stage_support(source, python=python) == result
    assert {p.relative_to(root).as_posix(): (p.stat().st_ino, p.read_bytes()) for p in root.rglob('*') if p.is_file()} == before
    assert set(manifest['files']) == set(s._sources(source))
    assert s.active_support() is None


def test_private_umask_does_not_change_declared_capsule_modes(support):
    source, python = support
    previous = os.umask(0o077)
    try:
        root = Path(s.stage_support(source, python=python)['root'])
    finally:
        os.umask(previous)
    s._verify(root)
    assert s.SUPPORT_ROOT.parent.stat().st_mode & 0o777 == 0o755
    assert s.SUPPORT_ROOT.stat().st_mode & 0o777 == 0o755
    assert (root / s.MANIFEST).stat().st_mode & 0o777 == 0o644
    assert (root / 'extensions/services/pixel-agent/host/cancellable-exec.sh').stat().st_mode & 0o777 == 0o755


@pytest.mark.parametrize('change', ['symlink', 'hardlink'])
def test_source_aliases_refuse_before_publication(support, change):
    source, python = support
    path = source / 'bin/pixel_access_protocol.py'
    if change == 'symlink':
        saved = path.with_name('original.py'); path.rename(saved); path.symlink_to(saved)
    else:
        os.link(path, path.with_name('other-link.py'))
    with pytest.raises((OSError, ValueError)):
        s.stage_support(source, python=python)
    assert not s.SUPPORT_ROOT.exists()


@pytest.mark.parametrize('change', ['bytes', 'mode', 'unknown', 'manifest'])
def test_published_drift_is_preserved_and_never_repaired(support, change):
    source, python = support
    root = Path(s.stage_support(source, python=python)['root'])
    target = root / 'bin/pixel_access_protocol.py'
    if change == 'bytes': target.write_bytes(target.read_bytes() + b'\n# changed\n')
    elif change == 'mode': target.chmod(0o666)
    elif change == 'unknown': (root / 'unknown.py').write_bytes(b'# unknown\n')
    else: (root / s.MANIFEST).write_bytes((root / s.MANIFEST).read_bytes() + b' ')
    before = {p.relative_to(root).as_posix(): (p.stat().st_ino, p.stat().st_mode, p.read_bytes()) for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError): s.stage_support(source, python=python)
    assert {p.relative_to(root).as_posix(): (p.stat().st_ino, p.stat().st_mode, p.read_bytes()) for p in root.rglob('*') if p.is_file()} == before


def test_source_cas_refuses_changed_original_inode_after_copy(support, monkeypatch):
    source, python = support
    write = os.write
    hit = []
    def changed(fd, body):
        result = write(fd, body)
        if not hit and '.pending/' in os.readlink('/proc/self/fd/' + str(fd)):
            hit.append(True)
            path = source / 'bin/pixel_access_protocol.py'
            replacement = path.with_name('replacement.py')
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        return result
    monkeypatch.setattr(os, 'write', changed)
    with pytest.raises(ValueError, match='source-changed'): s.stage_support(source, python=python)
    assert hit and not any(p.name.isalnum() and len(p.name) == 64 for p in s.SUPPORT_ROOT.iterdir())


def test_zero_write_refuses_without_spinning_or_publishing(support, monkeypatch):
    source, python = support
    monkeypatch.setattr(os, 'write', lambda fd, body: 0)
    with pytest.raises(ValueError, match='write-failed'): s.stage_support(source, python=python)
    assert not any(p.name.isalnum() and len(p.name) == 64 for p in s.SUPPORT_ROOT.iterdir())


@pytest.mark.parametrize('peer', ['access-install', 'native-uninstall'])
def test_fresh_isolated_entry_actual_peer_and_canonical_module_graph(support, peer):
    source, python = support
    result = s.stage_support(source, python=python)
    driver = r'''
import importlib.util,json,os,sys,urllib.request
from pathlib import Path
root=Path(__ROOT__)
spec=importlib.util.spec_from_file_location('pixel_native_installer_entry',root/'bin/pixel_native_installer_entry.py')
m=importlib.util.module_from_spec(spec); sys.modules[spec.name]=m; spec.loader.exec_module(m)
m.SUPPORT_ROOT=root.parent; m._acl=lambda fd:os.fstat(fd); sys.platform='darwin'
seen=[];peer_namespace=[]
def observe(frame,event,arg):
 if event=='call' and frame.f_code.co_filename==str(root/m.PEERS[__PEER__]) and frame.f_code.co_name=='main':
  seen.append((m.active_support(),list(sys.argv)))
  peer_namespace.append(frame.f_globals)
sys.setprofile(observe)
args=['--help'] if __PEER__=='native-uninstall' else ['--source','/original/code','--install-dir','/owner/data','--help']
try:m.main(['--peer',__PEER__,'--',*args])
except SystemExit as error: assert error.code==0
finally:sys.setprofile(None)
assert len(seen)==1 and seen[0][0]==dict(root=str(root),codeSha256=root.name,peer=__PEER__)
assert m.active_support() is None
if __PEER__=='access-install':
 assert seen[0][1][seen[0][1].index('--source')+1]==str(root)
 assert seen[0][1][seen[0][1].index('--install-dir')+1]=='/owner/data'
import pixel_access_bridge
from pixel_connections import custody
from pixel_provider import runtime_custody
assert runtime_custody.AccessError is pixel_access_bridge.AccessError
assert all(str(Path(mod.__file__)).startswith(str(root)+'/') for mod in (pixel_access_bridge,custody,runtime_custody))
if __PEER__=='access-install':
 pixel_access_bridge.platform.system=lambda:'Darwin'
 cold=object.__new__(pixel_access_bridge.LaunchdAccessBridge);cold.gateway_binding=None
 try:pixel_access_bridge.LaunchdAccessBridge.discover(cold)
 except pixel_access_bridge.AccessError as error:assert error.code=='gateway-launchd-binding-required'
 else:raise AssertionError('undiscovered gateway accepted')
 # Full ordinary discovery reads real Root plist/policy files and an actual
 # owner600 config. Only the OS launchd observation and plist location map are
 # the Linux/Darwin driver; no bridge field or custody proof is supplied.
 import pwd,grp,plistlib,hashlib,pixel_macos_custody,pixel_macos_policy,pixel_gateway_service
 owner=pwd.getpwnam('nobody'); group=grp.getgrgid(owner.pw_gid).gr_name
 data=root.parent/'cold-discovery';data.mkdir(mode=0o755)
 home=data/'runtime-home';home.mkdir(mode=0o700);os.chown(home,owner.pw_uid,owner.pw_gid)
 config=home/'selected-config.json';config.write_text(json.dumps({'gateway':{'port':19877,'auth':{'token':'synthetic-discovery-token'}}}));config.chmod(0o600);os.chown(config,owner.pw_uid,owner.pw_gid)
 policies={'sandboxed':b'(version 1) (deny default)\n','full-access':b'(version 1) (allow default)\n'}
 for mode,body in policies.items():
  path=data/('pixel-gateway.'+mode+'.sb');path.write_bytes(body);path.chmod(0o644)
 (data/'pixel-gateway.sb').write_bytes(policies['sandboxed']);(data/'pixel-gateway.sb').chmod(0o644)
 receipt=pixel_macos_policy.deployment_receipt(data,policies)
 label='com.ods.support-cold';target='system/'+label;virtual=Path('/Library/LaunchDaemons')/(label+'.plist')
 args=['/usr/bin/env','-i','HOME='+str(home),'OPENCLAW_CONFIG_PATH='+str(config),'OPENCLAW_STATE_DIR='+str(home),'/usr/bin/sandbox-exec','-f',receipt['active'],sys.executable,'/not-executed/openclaw.mjs','gateway','run','--port','19877']
 doc={'Label':label,'UserName':owner.pw_name,'GroupName':group,'ProgramArguments':args,'WorkingDirectory':str(home),'StandardOutPath':str(home/'stdout'),'StandardErrorPath':str(home/'stderr')}
 physical=data/'gateway.plist';physical.write_bytes(plistlib.dumps(doc));physical.chmod(0o644)
 original=pixel_macos_custody.protected_bytes
 def mapped(path,**kwargs):return original(physical if Path(path)==virtual else path,**kwargs)
 pixel_macos_custody.protected_bytes=mapped;pixel_macos_custody._require_no_acl=lambda fd:os.fstat(fd)
 definition=pixel_gateway_service.launchd_definition_digest(doc,pixel_access_bridge.AccessError)
 process={'uid':owner.pw_uid,'gid':owner.pw_gid,'executable':sys.executable}
 binding={'schemaVersion':1,'target':target,'plist':str(virtual),'definition':definition,'owner':owner.pw_name,'executable':sys.executable,'process':process}
 # Relocate only fixed physical paths into this private Root QA namespace.
 # The actual factory and actual discovery functions remain unchanged.
 state=data/'controller';state.mkdir(mode=0o700)
 ns=peer_namespace[0];ns['_launchd'].ACCESS_STATE=state;ns['_launchd'].GATEWAY_TARGET=target;ns['_launchd'].GATEWAY_PLIST=virtual;ns['GATEWAY_LAUNCHER']=Path(sys.executable)
 settings={'state_dir':str(state),'gateway_target':target,'gateway_plist':str(virtual),'owner':owner.pw_name,'openclaw_bin':sys.executable,'install_dir':'/owner/ods','gateway_process':process,'gateway_binding':binding,'gateway_port':19877,'gateway_policy':receipt}
 settingsfile=data/'settings.json';settingsfile.write_text(json.dumps(settings));settingsfile.chmod(0o600)
 observed=json.loads(pixel_macos_custody.protected_bytes(settingsfile))
 plan={'access_settings':observed,'key':b'fixture-edge-key','runtime_bundle':{'config_path':'/candidate/unselected.json','runtime_home':'/candidate/home','owner':'wrong-candidate-owner'}}
 bridge=ns['_bridge_from_recovery_plan'](plan,owner_name=owner.pw_name)
 assert not hasattr(bridge,'owner') and not hasattr(bridge,'home') and not hasattr(bridge,'_runtime_config_path')
 raw=target+' = {\n\tpath = '+str(virtual)+'\n\tprogram = /usr/bin/env\n\targuments = {\n'+''.join('\t\t'+arg+'\n' for arg in args)+'\t}\n\tworking directory = '+str(home)+'\n\tstdout path = '+str(home/'stdout')+'\n\tstderr path = '+str(home/'stderr')+'\n\tenvironment = {\n\t\tXPC_SERVICE_NAME => '+label+'\n\t}\n}\n'
 def command(argv,timeout=20):
  assert argv==['/bin/launchctl','print',target];return raw
 bridge.command=command
 with bridge.bounded(30):ns['_discover_source_installer_bridge'](bridge)
 assert bridge.owner.pw_uid==owner.pw_uid and bridge.home==home and bridge.home!=Path(owner.pw_dir)
 assert bridge._runtime_config_path==config and bridge.native_port==19877 and bridge.surface=='darwin'
 # The initial publication helper must create its real bounded lease and
 # discover the new cold bridge before entering separately qualified runtime
 # selection/publication. Those latter producer boundaries are observed here;
 # actual runtime/package/publication proofs have independent real-file suites.
 events=[];selected=object();deployment=object()
 def ready(current):
  assert pixel_access_bridge.LaunchdAccessBridge._native_runtime_lease(current) is not None
  assert current.owner.pw_uid==owner.pw_uid and current.home==home and current._runtime_config_path==config
 def inspect_runtime(instance):
  ready(instance.bridge);events.append('inspect-after-discover')
  return {'runtimeRoot':'/qualified/runtime','sourceRoot':'/qualified/ods-source'},'a'*64
 def deployment_runtime(current):
  ready(current);events.append('deployment-after-discover');return deployment
 def select_runtime(current,value):
  ready(current);assert value is deployment;events.append('select-after-discover');return selected
 runtime_custody.RuntimeCustody.inspect=inspect_runtime
 pixel_access_bridge.LaunchdAccessBridge.connection_deployment=deployment_runtime
 pixel_access_bridge.LaunchdAccessBridge.command=lambda self,argv,timeout=20:command(argv,timeout)
 custody.native_source_selection=select_runtime
 original_spec=importlib.util.spec_from_file_location
 def observed_spec(name,*args,**kwargs):
  spec=original_spec(name,*args,**kwargs)
  if name=='installed_source_publication':
   original_exec=spec.loader.exec_module
   def execute(module):
    original_exec(module)
    def published(value):
     assert value is selected;events.append('publish-after-discover')
    module.publish_kit=published
   spec.loader.exec_module=execute
  return spec
 importlib.util.spec_from_file_location=observed_spec
 initial_plan=dict(plan,owner=owner,runtime_bundle={'connection_sidefiles':{},'destination':'/qualified'})
 ns['_verify_source_runtime'](initial_plan)
 assert events==['inspect-after-discover','deployment-after-discover','select-after-discover','publish-after-discover']
 # The same genuine function loaded from an owner file must still fail its
 # unchanged program custody gate. Never modify a module's __file__.
 ownerfile=root.parent/'owner-pixel-access-bridge.py'
 ownerfile.write_bytes(Path(pixel_access_bridge.__file__).read_bytes());os.chown(ownerfile,65534,65534)
 probe=importlib.util.spec_from_file_location('_owner_program_probe',ownerfile)
 foreignmod=importlib.util.module_from_spec(probe);probe.loader.exec_module(foreignmod)
 cold=object.__new__(foreignmod.LaunchdAccessBridge);cold.gateway_binding=None
 try:foreignmod.LaunchdAccessBridge.discover(cold)
 except foreignmod.AccessError as error:assert error.code=='root-program-custody-required'
 else:raise AssertionError('owner program accepted')
print('QUALIFIED-CANONICAL-ENTRY')
'''.replace('__ROOT__', repr(result['root'])).replace('__PEER__', repr(peer))
    run = subprocess.run([str(python), '-I', '-B', '-c', driver], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
    assert 'QUALIFIED-CANONICAL-ENTRY' in run.stdout


@pytest.mark.parametrize('spoofed_file', [False, True])
def test_foreign_loaded_alias_refuses_without_replacing_global_modules(support, spoofed_file):
    source, python = support
    result = s.stage_support(source, python=python)
    driver = r'''
import importlib.util,os,sys,types,urllib.request
from pathlib import Path
root=Path(__ROOT__)
spec=importlib.util.spec_from_file_location('pixel_native_installer_entry',root/'bin/pixel_native_installer_entry.py')
m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
m.SUPPORT_ROOT=root.parent;m._acl=lambda fd:os.fstat(fd);sys.platform='darwin'
foreign=types.ModuleType('pixel_access_bridge');foreign.__file__=str(root/'bin/pixel_access_bridge.py') if __SPOOFED__ else '/owner/checkout/pixel_access_bridge.py'
sys.modules['pixel_access_bridge']=foreign;old=list(sys.path)
try:m.main(['--peer','access-install','--','--help'])
except ValueError as error:assert str(error)=='native-installer-support-foreign-module'
else:raise AssertionError('foreign module accepted')
assert sys.modules['pixel_access_bridge'] is foreign and sys.path==old and m.active_support() is None
'''.replace('__ROOT__', repr(result['root'])).replace('__SPOOFED__', repr(spoofed_file))
    run = subprocess.run([str(python), '-I', '-B', '-c', driver], capture_output=True, text=True, timeout=60)
    assert run.returncode == 0, run.stderr
