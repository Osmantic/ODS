"""Reviewed boundary tests, including real owned-process SIGTERM cleanup."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider import client as mod
from test_connection import BASE_CONN
from copy import deepcopy

pytestmark = pytest.mark.skipif(os.name != 'posix',reason='owner-private POSIX adapter')


def test_client_pin_tracks_parent_installer():
    root = Path(__file__).resolve().parents[2]
    phase = (root/'installers/phases/06-directories.sh').read_text()
    assert f'PIXEL_SOURCE_REF "{mod.PIXEL_COMMIT}"' in phase
    assert f'PIXEL_SOURCE_REF={mod.PIXEL_COMMIT}' in (root/'.env.example').read_text()


@pytest.fixture(params=(
    '70f44c90ac40b8409ebc965becc5b085a053e270',
    '9409d1ae894394a4848bf5b41a6323e64c577f06',
    mod.PIXEL_COMMIT,
), ids=('legacy', 'native-search', 'current'))
def client_dir(tmp_path,request):
    tmp_path.chmod(0o700)
    (tmp_path/'pixel-source').mkdir(mode=0o700)
    (tmp_path/'state').mkdir(mode=0o700)
    conn = deepcopy(BASE_CONN)
    conn['expiresAt'] = int(time.time())+3600
    contents = {'connection.json':mod._json(conn),'onboarding.json':b'{}',
        'pixel-source/.env':f'OPENCLAW_BIN={sys.executable}\nPIXEL_MODEL_REASONING=false\n'.encode(),
        'state/openclaw.json':b'{}'}
    for name,content in contents.items():
        mod._write_private(tmp_path/name,content)
    record = dict(schemaVersion=1,pixelCommit=request.param,openclawVersion=mod.OPENCLAW_VERSION,
        status='prepared-not-activated',execution='client-owned',agentId='pixel-client-'+'a'*16,
        files={name:hashlib.sha256(content).hexdigest() for name,content in contents.items()})
    mod._write_private(tmp_path/'prepared.json',mod._json(record))
    return tmp_path


def test_load_preserves_prepared_client(client_dir):
    before = {path.relative_to(client_dir):path.read_bytes()
              for path in client_dir.rglob('*') if path.is_file()}
    directory,record = mod.load_client(client_dir)
    assert directory == client_dir
    assert record['pixelCommit'] in mod.PREPARED_PIXEL_COMMITS
    assert {path.relative_to(client_dir):path.read_bytes()
            for path in client_dir.rglob('*') if path.is_file()} == before


@pytest.mark.parametrize('commit',[None,True,1,[],{},'main','0'*40,
    '70f44c90ac40b8409ebc965becc5b085a053e271',mod.PIXEL_COMMIT.upper()])
def test_unknown_prepared_source_rejected_without_changes(client_dir,commit):
    path = client_dir/'prepared.json'
    record = json.loads(path.read_bytes())
    record['pixelCommit'] = commit
    path.write_bytes(mod._json(record))
    before = path.read_bytes()
    with pytest.raises(mod.StoreError,match='invalid-client-record'):
        mod.load_client(client_dir)
    assert path.read_bytes() == before
    assert not (client_dir/'runs').exists()


def test_load_and_drift(client_dir):
    assert mod.load_client(client_dir)[0] == client_dir
    (client_dir/'connection.json').write_bytes(b'changed')
    with pytest.raises(mod.StoreError,match='client-configuration-changed'):
        mod.load_client(client_dir)


@pytest.mark.parametrize('mode',[0o640,0o644,0o666])
def test_file_permissions(client_dir,mode):
    path = client_dir/'connection.json'
    path.chmod(mode)
    with pytest.raises(mod.StoreError,match='unsafe-client-file'):
        mod.read_private(path)


@pytest.mark.parametrize('link',['symlink','hardlink'])
def test_file_links(client_dir,link):
    target = client_dir/'linked'
    if link == 'symlink':
        target.symlink_to(client_dir/'connection.json')
    else:
        target.hardlink_to(client_dir/'connection.json')
    with pytest.raises(mod.StoreError,match='unsafe-client-file'):
        mod.read_private(target)


def test_oversized_private_file(tmp_path):
    mod._write_private(tmp_path/'large',b'x'*(mod.MAX_BYTES+1))
    with pytest.raises(mod.StoreError,match='unsafe-client-file'):
        mod.read_private(tmp_path/'large')


@pytest.mark.parametrize('directory',['.','state','pixel-source'])
def test_directory_permissions(client_dir,directory):
    (client_dir/directory).chmod(0o755)
    with pytest.raises(mod.StoreError,match='unsafe-client-directory'):
        mod.load_client(client_dir)


def test_invalid_image_before_any_effect(tmp_path,monkeypatch):
    monkeypatch.setattr(mod,'_command',lambda *a,**kw: pytest.fail('spawned before validation'))
    with pytest.raises(mod.StoreError,match='invalid-sandbox-image'):
        mod.prepare_client({},tmp_path/'new',confirmed_endpoint='',pixel_repository='',
            openclaw_bin='/bin/true',reasoning=False,sandbox_image='image\nINJECTED=1')
    assert not (tmp_path/'new').exists()


def test_existing_directory_preserved(client_dir,monkeypatch):
    monkeypatch.setattr(mod,'_command',lambda *a,**kw: pytest.fail('spawned before validation'))
    with pytest.raises(mod.StoreError,match='client-directory-exists'):
        mod.prepare_client({},client_dir,confirmed_endpoint='',pixel_repository='',
            openclaw_bin='/bin/true',reasoning=False)
    assert mod.load_client(client_dir)


def test_revoked_preflight_no_spawn(client_dir,monkeypatch):
    def denied(*a,**kw):
        raise mod.StoreError('connection-denied')
    monkeypatch.setattr(mod,'probe_connection',denied)
    monkeypatch.setattr(mod.subprocess,'Popen',lambda *a,**kw: pytest.fail('spawned revoked turn'))
    with pytest.raises(mod.StoreError,match='connection-denied'):
        mod.run_client(client_dir,'hello')
    assert not (client_dir/'runs').exists()


def test_client_run_admission_lock_precedes_model_call(client_dir,monkeypatch):
    monkeypatch.setattr(mod,'probe_connection',lambda *a,**kw: pytest.fail('busy client made model request'))
    with mod._run_lock(client_dir):
        with pytest.raises(mod.StoreError,match='client-busy'):
            mod.run_client(client_dir,'hello')
    assert not (client_dir/'runs').exists()
    assert (client_dir/'.client-run.lock').is_file()


def test_agent_inherits_admission_if_supervisor_closes_its_handle(client_dir):
    with mod._run_lock(client_dir) as fd:
        child = subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.buffer.read(1)'],
            pass_fds=(fd,),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        with pytest.raises(mod.StoreError,match='client-busy'):
            with mod._run_lock(client_dir):
                pass
        child.communicate(b'x',timeout=3)
        assert child.returncode == 0
        with mod._run_lock(client_dir):
            pass
    finally:
        if child.poll() is None:
            child.kill(); child.wait()


def test_ambient_profile_not_inherited(client_dir,monkeypatch):
    for name in ('OPENCLAW_AGENT_DIR','OPENCLAW_PROFILE','PIXEL_AGENT_TOOL_ALLOWLIST','XDG_DATA_HOME'):
        monkeypatch.setenv(name,'must-not-be-used')
    result = mod._render_environment(client_dir/'pixel-source',client_dir)
    assert 'must-not-be-used' not in result.values()
    assert result['OPENCLAW_CONFIG_PATH'] == str(client_dir/'state/openclaw.json')


@pytest.mark.parametrize('slow',[False,True])
def test_turn_evidence_and_timeout(client_dir,monkeypatch,slow):
    killed = []
    captured = []
    previous = signal.getsignal(signal.SIGTERM)
    class Child:
        pid = 987654321
        returncode = -9 if slow else 0
        def __init__(self,*a,**kw):
            captured.append((a,kw))
        def communicate(self,timeout=None):
            if slow and timeout is not None:
                raise subprocess.TimeoutExpired('fake',timeout)
            return b'{"ok":true}',b''
    monkeypatch.setattr(mod.subprocess,'Popen',Child)
    monkeypatch.setattr(mod,'_command',lambda *a,**kw: b'OpenClaw 2026.6.33 (fixture)')
    monkeypatch.setattr(mod,'probe_connection',lambda *a,**kw: {})
    monkeypatch.setattr(mod.os,'killpg',lambda pid,sig: killed.append((pid,sig)))
    result = mod.run_client(client_dir,'hello',timeout=1)
    assert result['status'] == ('interrupted' if slow else 'process-exited')
    assert result['result'] == {'ok':True}
    assert (Path(result['evidence'])/'result.json').is_file()
    assert mod.read_private(Path(result['evidence'])/'message.txt') == b'hello'
    assert captured[0][1]['start_new_session'] is True
    assert len(captured[0][1]['pass_fds']) == 1
    assert captured[0][0][0][-2:] == ['--thinking','off']
    assert killed == ([(Child.pid,signal.SIGTERM),(Child.pid,signal.SIGKILL)] if slow else [])
    assert signal.getsignal(signal.SIGTERM) == previous


def test_runtime_upgrade_rejected(client_dir,monkeypatch):
    monkeypatch.setattr(mod,'probe_connection',lambda *a,**kw: {})
    monkeypatch.setattr(mod,'_command',lambda *a,**kw: b'OpenClaw changed-version')
    with pytest.raises(mod.StoreError,match='unsupported-openclaw-version'):
        mod.run_client(client_dir,'hello')
    assert not (client_dir/'runs').exists()


def test_sigterm_reaps_actual_owned_group(client_dir,tmp_path):
    marker = tmp_path/'child-pids.json'
    runtime = tmp_path/'fake-openclaw'
    runtime.write_text('#!'+sys.executable+'\n'+'''
import json,os,pathlib,signal,subprocess,sys,time
if '--version' in sys.argv:
    print('OpenClaw 2026.6.33 (fixture)'); raise SystemExit
child = subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'])
def stop(*args):
    child.wait(timeout=3)
    raise SystemExit(0)
signal.signal(signal.SIGTERM,stop)
pathlib.Path(os.environ['TEST_CHILD_MARKER']).write_text(json.dumps([os.getpid(),child.pid]))
time.sleep(120)
''')
    runtime.chmod(0o700)
    launcher = tmp_path/'launcher.py'
    launcher.write_text('from pixel_provider import client as m\nimport sys\n'
        'm.probe_connection=lambda *a,**kw: {}\n'
        'original=m._render_environment\n'
        f'm._render_environment=lambda s,d: dict(original(s,d),OPENCLAW_BIN={str(runtime)!r})\n'
        'm.run_client(sys.argv[1],"test",timeout=120)\n')
    env = dict(os.environ,PYTHONPATH=str(Path(mod.__file__).parents[1]),TEST_CHILD_MARKER=str(marker))
    owner = subprocess.Popen([sys.executable,str(launcher),str(client_dir)],env=env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    pids = []
    try:
        deadline = time.monotonic()+10
        while not marker.exists() and owner.poll() is None and time.monotonic()<deadline:
            time.sleep(.02)
        assert marker.exists(),owner.communicate(timeout=1)
        pids = json.loads(marker.read_text())
        assert os.getpgid(pids[0]) == pids[0]
        owner.send_signal(signal.SIGTERM)
        out,err = owner.communicate(timeout=10)
        assert owner.returncode == 0,(out,err)
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid,0)
        record = json.loads(next((client_dir/'runs').glob('*/result.json')).read_text())
        assert record['status'] == 'interrupted'
    finally:
        if owner.poll() is None:
            owner.kill(); owner.wait()
        for pid in pids:
            try:
                os.kill(pid,signal.SIGKILL)
            except ProcessLookupError:
                pass
