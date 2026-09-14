import json
import os
from pathlib import Path
import sys
import subprocess

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'bin'))
from pixel_provider import advice_runtime as runtime
from pixel_provider.advice_jobs import AdvisoryJobs
from pixel_provider.store import StoreError
from test_advice import request, saved as saved

pytestmark=pytest.mark.skipif(os.name != 'posix',reason='POSIX private runtime')


@pytest.fixture
def provision(tmp_path,monkeypatch):
    root=tmp_path/'providers'
    root.mkdir(mode=0o700)
    # This is a simulated provisioner, not interpreter qualification. Hosted
    # tool-cache ownership/modes are outside our control; never relax production
    # custody or chmod a shared Python installation to make this fixture pass.
    binary=root/'fixture-python'
    binary.write_bytes(b'fictional interpreter, never executed')
    binary.chmod(0o700)
    calls=[]
    def install(command,**kwargs):
        calls.append(command)
        if 'venv' in command:
            target=Path(command[-1])
            target.mkdir(mode=0o700)
            (target/'bin').mkdir(mode=0o700)
            (target/'bin'/'python').symlink_to(binary)
        if '--report' in command:
            report=Path(command[command.index('--report')+1])
            report.write_text(json.dumps({'install':[dict(metadata={'name':'httpx','version':'0.28.1'},
                download_info={'archive_info':{'hashes':{'sha256':'a'*64}}})]}))
    monkeypatch.setattr(runtime,'install_command',install)
    monkeypatch.setattr(runtime,'run_worker',lambda *args,**kwargs:dict(schemaVersion=1,ready=True))
    return root,calls


def prepare(root,revision=0,**changes):
    return runtime.prepare_runtime(root,**dict(dict(python=str(root/'fixture-python'),expected_revision=revision,confirmed=True),**changes))


def test_explicit_optional_prepare_idempotence_and_separate_revision(provision):
    root,calls=provision
    assert runtime.runtime_status(root)==dict(status='missing',revision=0,runtimeId=None)
    result=prepare(root)
    assert result['status']=='ready' and result['revision']==1
    assert runtime.runtime_status(root)==result
    before=list(calls)
    assert prepare(root,1)==result and calls==before
    with pytest.raises(StoreError,match='stale-revision'):
        prepare(root,0)
    assert not (root/'provider-config.json').exists()
    assert (root/'advice-runtime.json').stat().st_mode & 0o777 == 0o600
    assert (root/'advice-runtimes'/result['runtimeId']).stat().st_mode & 0o777 == 0o700


def test_actual_probe_rechecks_captured_receipt_without_retaking_store_lock(provision):
    import fcntl
    root, _ = provision
    prepare(root)
    receipt = runtime.RuntimeStore(root).load()
    launcher = Path(__file__).resolve().parents[2] / 'bin/ods-pixel-route-lease'
    def probe(value):
        result = subprocess.run([sys.executable, '-I', '-S', '-B', str(launcher), '--check-runtime'],
            input=json.dumps({'providerDirectory': str(root), 'receipt': value}),
            capture_output=True, text=True, timeout=5, check=True)
        assert not result.stderr
        return json.loads(result.stdout)
    with (root / '.provider-config.lock').open('r+') as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert probe(receipt) == {'ready': True}
        source = root / 'advice-runtimes' / receipt['runtime']['id'] / 'source/pixel_provider/advice.py'
        source.write_bytes(source.read_bytes() + b'\n# real drift\n')
        assert probe(receipt) == {'ready': False}
        assert probe(dict(receipt, schemaVersion=2)) == {'ready': False}


@pytest.mark.parametrize('changes,code',[
    ({'confirmed':False},'confirmation'),({'python':'python3'},'explicit-python'),
    ({'expected_revision':True},'stale-revision'),
])
def test_install_requires_confirmation_and_exact_revision(provision,changes,code):
    root,calls=provision
    with pytest.raises(StoreError,match=code):
        prepare(root,**changes)
    assert not calls and not (root/'advice-runtimes').exists()


def test_mtime_preserving_runtime_tamper_fails_closed_and_can_reprepare(provision):
    root,_=provision
    first=prepare(root)
    path=root/'advice-runtimes'/first['runtimeId']/'source'/'pixel_provider'/'advice.py'
    old=path.stat()
    path.write_bytes(path.read_bytes()+b'\n#tampered\n')
    os.utime(path,ns=(old.st_atime_ns,old.st_mtime_ns))
    assert runtime.runtime_status(root)['status']=='drift'
    with pytest.raises(StoreError,match='drift'):
        runtime.resolve_runtime(root)
    second=prepare(root,1)
    assert second['revision']==2 and second['runtimeId']!=first['runtimeId']
    assert path.exists(), 'old evidence was destroyed'


def test_foreign_symlink_and_public_runtime_fail_closed(provision,tmp_path):
    root,_=provision
    first=prepare(root)
    leaf=root/'advice-runtimes'/first['runtimeId']
    (leaf/'foreign').symlink_to('/etc/passwd')
    assert runtime.runtime_status(root)['status']=='drift'
    (leaf/'foreign').unlink()
    leaf.chmod(0o755)
    assert runtime.runtime_status(root)['status']=='drift'


def test_failed_repair_keeps_pointer_and_inactive_evidence(provision,monkeypatch):
    root,_=provision
    first=prepare(root)
    leaf=root/'advice-runtimes'/first['runtimeId']
    (leaf/'requirements.txt').write_text('changed')
    old=(root/'advice-runtime.json').read_bytes()
    def fail(*args,**kwargs):
        raise StoreError('advice-runtime-install-failed')
    monkeypatch.setattr(runtime,'run_worker',fail)
    with pytest.raises(StoreError):
        prepare(root,1)
    assert (root/'advice-runtime.json').read_bytes()==old
    assert len(list((root/'advice-runtimes').glob('runtime-*')))==2


def test_missing_runtime_does_not_claim_or_read_capsule(saved):
    root,_=saved
    body=request()
    with pytest.raises(StoreError,match='advice-runtime-missing'):
        AdvisoryJobs(root).start(body)
    assert not (root/'advice-jobs'/body['requestId']).exists()


def test_interpreter_custody_drift_before_credential_pass(provision,monkeypatch):
    root,_=provision
    prepare(root)
    original=runtime.digest_file
    binary=root/'fixture-python'
    monkeypatch.setattr(runtime,'digest_file',lambda path:'f'*64 if path==binary else original(path))
    assert runtime.runtime_status(root)['status']=='drift'
    with pytest.raises(StoreError):
        runtime.resolve_runtime(root)


@pytest.mark.parametrize('unsafe',['group-write','other-write','hardlink'])
def test_unsafe_interpreter_rejected_before_provision(provision,unsafe):
    root,calls=provision
    binary=root/'fixture-python'
    if unsafe=='hardlink':
        os.link(binary,root/'second-name')
    else:
        binary.chmod(0o720 if unsafe=='group-write' else 0o702)
    with pytest.raises(StoreError,match='advice-runtime-unsafe'):
        prepare(root)
    assert not calls and not (root/'advice-runtimes').exists()
