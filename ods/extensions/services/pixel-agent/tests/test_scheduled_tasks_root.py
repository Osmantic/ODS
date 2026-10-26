"""Real root-owned bundle, owner UID drop, private ledger and pinned native RPC.

Explicit Linux root lane only. A disposable locked, non-login account owns the
fixture; the runtime package is bind-mounted read-only. Installed ODS state and
services are never selected. The ordinary host suite skips this qualification.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

if sys.platform != 'linux':
    raise unittest.SkipTest('Protected user and read-only runtime qualification is Linux-only')
import pwd  # noqa: E402

ROOT = Path(__file__).resolve().parents[4]
PACKAGE = os.environ.get('OPENCLAW_PACKAGE_DIR')
QUALIFY = sys.platform == 'linux' and os.geteuid() == 0 and PACKAGE

GATEWAY = r'''
import json,os,secrets,socket,subprocess,sys,time
from pathlib import Path
home,binary,owner_name=Path(sys.argv[1]),sys.argv[2],sys.argv[3]
state=home/'.openclaw';state.mkdir(mode=0o700)
with socket.socket() as listener:
    listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
config={'gateway':{'mode':'local','port':port,'bind':'loopback','auth':{'mode':'token','token':secrets.token_hex(32)}},
        'logging':{'file':str(home/'gateway.log')},'plugins':{'allow':[],'entries':{}},
        'agents':{'defaults':{'heartbeat':{'every':'0m'}},'list':[{'id':'pixel','default':True,'workspace':str(home/'workspace')}]}}
path=state/'openclaw.json';path.write_text(json.dumps(config));path.chmod(0o600)
env={'PATH':str(Path(binary).parent)+':/usr/local/bin:/usr/bin:/bin','HOME':str(home),'USER':owner_name,'LOGNAME':owner_name,'NO_COLOR':'1',
     'OPENCLAW_CONFIG_PATH':str(path),'OPENCLAW_STATE_DIR':str(state)}
with (home/'startup.log').open('w') as output:
    child=subprocess.Popen([binary,'gateway','run','--port',str(port)],cwd=home,env=env,stdout=output,stderr=subprocess.STDOUT)
    try:
        print(json.dumps({'port':port,'uid':os.geteuid()}),flush=True)
        # A separate stop file keeps the gateway's stdin independent of its
        # fixture supervisor. Closing a CLI pipe must not stop the gateway.
        while not (home/'stop-fixture').exists():
            if child.poll() is not None:raise RuntimeError('private-gateway-exited')
            time.sleep(.1)
    finally:
        if child.poll() is None:
            child.terminate()
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:child.kill();child.wait(timeout=10)
'''

DRIVER = r'''
import json,os,pwd,sys,time,uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import pixel_access_bridge as bridge
value=json.loads(sys.stdin.buffer.read(4096))
owner=pwd.getpwnam(value['owner']);home=Path(owner.pw_dir)
assert owner.pw_uid>0 and str(home)==value['home']
adapter=bridge.SystemdAccessBridge(home/'ods','o'*64)
adapter.owner,adapter.home,adapter.binary=owner,home,value['binary']
def operation(request):
    snapshot=adapter.worker('settings-status')
    return adapter.worker('schedules',config_hash=snapshot['configSha256'],gateway_port=value['port'],schedule_request=request)
deadline=time.monotonic()+35
while True:
    try:
        assert operation({'action':'list','offset':0})['items']==[]
        break
    except bridge.AccessError:
        if time.monotonic()>=deadline:raise RuntimeError('native-fixture-not-ready') from None
        time.sleep(.2)
try:
    adapter.worker('schedules',config_hash='0'*64,gateway_port=value['port'],schedule_request={'action':'list','offset':0})
    raise AssertionError('Changed config accepted')
except bridge.AccessError as error:
    assert error.code=='controller-task-config-changed'
created=operation({'action':'create','requestId':str(uuid.uuid4()),'name':'Root owner pipe fixture',
    'task':'Future-only fixture. Do not execute a model.','schedule':{'kind':'at','at':'2030-01-01T09:00:00Z'}})
assert created['outcome']=='applied'
task=created['task'];ledger=home/'.openclaw/.ods-scheduled-tasks/tasks.json'
assert ledger.stat().st_uid==owner.pw_uid and ledger.stat().st_mode&0o777==0o600
paused=operation({'action':'set-enabled','id':task['id'],'revision':task['revision'],'enabled':False})
assert paused['task']['enabled'] is False
removed=operation({'action':'remove','id':task['id'],'revision':paused['task']['revision']})
assert removed['task']['removed'] is True
assert operation({'action':'archived','offset':0})['items'][0]['id']==task['id']
module=Path(__file__).with_name('pixel_schedules.py');module.chmod(0o666)
try:
    try:
        operation({'action':'list','offset':0})
        raise AssertionError('Writable protected code accepted')
    except bridge.AccessError as error:
        assert error.code=='owner-protocol-failed'
finally:module.chmod(0o644)
print(json.dumps({'rootOwnedBundle':True,'realOwnerWorker':True,'privateLedger':True,
    'createPauseRemove':True,'changedConfigRefused':True,'writableCodeRefused':True}))
'''


@unittest.skipUnless(QUALIFY, 'Explicit Linux root qualification needs OPENCLAW_PACKAGE_DIR')
class NativeOwnerRootTests(unittest.TestCase):
    def test_protected_owner_worker_against_read_only_native_runtime(self):
        package = Path(PACKAGE).resolve(strict=True)
        metadata = json.loads((package / 'package.json').read_bytes())
        self.assertEqual((metadata['name'], metadata['version']), ('openclaw', '2026.6.33'))
        node = Path(os.environ.get('ODS_SCHEDULE_NODE') or shutil.which('node')).resolve(strict=True)
        name = 'odsqa-' + uuid.uuid4().hex[:10]
        # Protected ancestors are part of the actual owner-worker admission.
        # /tmp is deliberately unsuitable for this root program bundle.
        base = Path(tempfile.mkdtemp(prefix='ods-schedules-qa-', dir='/usr/local/lib'))
        created = mounted = False
        child = owner = None
        try:
            base.chmod(0o755)
            home = base / 'owner'
            subprocess.run(['/usr/sbin/useradd', '--system', '--user-group', '--no-create-home',
                '--home-dir', str(home), '--shell', '/usr/sbin/nologin', name], check=True)
            created = True
            owner = pwd.getpwnam(name)
            self.assertGreater(owner.pw_uid, 0)
            self.assertEqual(owner.pw_dir, str(home))
            home.mkdir(mode=0o700)
            os.chown(home, owner.pw_uid, owner.pw_gid)
            runtime = base / 'runtime'
            runtime.mkdir(mode=0o755)
            subprocess.run(['/usr/bin/mount', '--bind', str(package), str(runtime)], check=True)
            mounted = True
            subprocess.run(['/usr/bin/mount', '-o', 'remount,bind,ro', str(runtime)], check=True)
            options = subprocess.check_output(['/usr/bin/findmnt', '-T', str(runtime), '-n', '-o', 'OPTIONS'], text=True)
            self.assertIn('ro', options.strip().split(','))
            executables = base / 'bin'
            executables.mkdir(mode=0o755)
            (executables / 'node').symlink_to(node)
            (executables / 'openclaw').symlink_to(runtime / 'openclaw.mjs')
            binary = str(executables / 'openclaw')
            programs = base / 'programs'
            programs.mkdir(mode=0o755)
            for source_dir in (ROOT / 'bin', ROOT / 'extensions/services/pixel-agent/host'):
                for source in source_dir.iterdir():
                    if source.is_file() and source.suffix in ('.py', '.mjs'):
                        shutil.copyfile(source, programs / source.name)
            for package_name in ('pixel_settings', 'pixel_provider'):
                shutil.copytree(ROOT / 'bin' / package_name, programs / package_name,
                    ignore=shutil.ignore_patterns('__pycache__'))
            for path in programs.rglob('*'):
                self.assertFalse(path.is_symlink())
                path.chmod(0o755 if path.is_dir() else 0o644)
            helper, driver = programs / 'fixture_gateway.py', programs / 'fixture_driver.py'
            helper.write_text(GATEWAY)
            driver.write_text(DRIVER)
            helper.chmod(0o644)
            driver.chmod(0o644)
            env = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': str(home), 'USER': name, 'LOGNAME': name, 'LANG': 'C.UTF-8'}
            child = subprocess.Popen(['/usr/bin/python3', '-I', str(helper), str(home), binary, name],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, env=env, cwd=home, user=owner.pw_uid, group=owner.pw_gid, extra_groups=[])
            ready = json.loads(child.stdout.readline(4096))
            self.assertEqual(ready['uid'], owner.pw_uid)
            result = subprocess.run([sys.executable, '-I', str(driver)], timeout=100, capture_output=True,
                text=True, input=json.dumps({'home': str(home), 'owner': name, 'binary': binary, 'port': ready['port']}))
            self.assertEqual(result.returncode, 0, result.stderr[-3000:])
            self.assertEqual(json.loads(result.stdout), {key: True for key in ('rootOwnedBundle', 'realOwnerWorker',
                'privateLedger', 'createPauseRemove', 'changedConfigRefused', 'writableCodeRefused')})
        finally:
            if child and child.poll() is None:
                (base / 'owner/stop-fixture').touch()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    child.wait(timeout=10)
            if child:
                child.stdout.close()
            if created:
                current = pwd.getpwnam(name)
                self.assertEqual((current.pw_uid, current.pw_dir), (owner.pw_uid, str(base / 'owner')))
                subprocess.run(['/usr/bin/pkill', '-TERM', '-u', str(owner.pw_uid)], stderr=subprocess.DEVNULL)
                deadline = time.monotonic() + 5
                while subprocess.run(['/usr/bin/pgrep', '-u', str(owner.pw_uid)], stdout=subprocess.DEVNULL).returncode == 0 and time.monotonic() < deadline:
                    time.sleep(.1)
                if subprocess.run(['/usr/bin/pgrep', '-u', str(owner.pw_uid)], stdout=subprocess.DEVNULL).returncode == 0:
                    subprocess.run(['/usr/bin/pkill', '-KILL', '-u', str(owner.pw_uid)], check=True)
                subprocess.run(['/usr/sbin/userdel', name], check=True)
            if mounted and subprocess.run(['/usr/bin/umount', str(base / 'runtime')]).returncode:
                # Never recursively remove a live bind mount of the package.
                base.chmod(0o700)
                raise RuntimeError('Test mount retained; protected fixture preserved for cleanup')
            self.assertEqual(base.parent, Path('/usr/local/lib'))
            self.assertTrue(base.name.startswith('ods-schedules-qa-') and not base.is_symlink())
            shutil.rmtree(base)


if __name__ == '__main__':
    unittest.main()
