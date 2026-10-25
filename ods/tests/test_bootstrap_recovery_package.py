"""Portable artifact controls; physical cgroup qualification is separate."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ODS = Path(__file__).resolve().parents[1]
SOURCE = ODS / 'recovery/bootstrap_current'
BUILDER = ODS / 'scripts/build-bootstrap-recovery-package.py'


def build(path):
    spec = importlib.util.spec_from_file_location('recovery_builder', BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.build(path)


def test_artifact_reproducible_and_every_program_bound(tmp_path):
    first, second = tmp_path / 'first.tar', tmp_path / 'second.tar'
    receipt = build(first)
    build(second)
    assert first.read_bytes() == second.read_bytes()
    assert receipt['sha256'] == hashlib.sha256(first.read_bytes()).hexdigest()
    assert receipt['installed'] is False
    with tarfile.open(first) as archive:
        members = archive.getmembers()
        assert len(members) == 11
        assert all(item.isfile() and item.uid == item.gid == item.mtime == 0 and item.mode == 0o644 for item in members)
        files = {Path(item.name).name: archive.extractfile(item).read() for item in members}
    manifest = json.loads(files.pop('manifest.json'))
    assert manifest == {'version': 1, 'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    assert set(files) == {path.name for path in SOURCE.glob('*') if path.suffix in ('.py', '.json')}
    compatibility = json.loads(files['compatibility.json'])
    assert len(compatibility['bundle']) == 32
    assert hashlib.sha256(compatibility['unitText'].encode()).hexdigest() == compatibility['unitSha256']
    with pytest.raises(FileExistsError):
        build(first)


def test_loader_requires_isolation_before_import_or_host_access():
    result = subprocess.run([sys.executable, str(SOURCE / 'loader.py'), 'preflight'], capture_output=True, text=True)
    assert result.returncode != 0
    assert result.stderr.strip() == 'isolated-python-required'


@pytest.mark.skipif(sys.platform != 'linux' or os.geteuid() != 0, reason='Root owner-drop transport; qualified in isolated root fixture')
def test_owner_output_is_bounded_while_receiving_and_timeout_reaps():
    code = r'''
import os,sys,time
sys.path.insert(0,sys.argv[1])
from adapters import bounded_owner
from custody import Refused
fd=os.memfd_create('inert-qualification')
try:
 for source,wanted,timeout in [('print("x"*1100000)','owner-output-limit',5),('import time;time.sleep(5)','owner-operation-timeout',.1)]:
  before=time.monotonic()
  try:
   bounded_owner([sys.executable,'-I','-c',source],b'',uid=os.getuid(),gid=os.getgid(),environment={'PATH':'/usr/bin:/bin'},program=fd,timeout=timeout)
  except Refused as error:assert str(error)==wanted
  else:raise AssertionError('unbounded owner accepted')
  assert time.monotonic()-before<3
 try:os.waitpid(-1,os.WNOHANG)
 except ChildProcessError:pass
 else:raise AssertionError('owner child not reaped')
finally:os.close(fd)
'''
    subprocess.run([sys.executable, '-B', '-I', '-c', code, str(SOURCE)], check=True, timeout=10)


@pytest.mark.skipif(sys.platform != 'linux', reason='POSIX installer lock')
def test_lock_key_matches_unchanged_installer_cksum():
    code = r'''
import subprocess,sys
sys.path.insert(0,sys.argv[1])
from controller import lock_key
for path in ('/home/owner/ODS','/home/owner/ODS with spaces','/opt/local/ODS'):
 result=subprocess.check_output(['cksum'],input=path.encode()+b'\0').split()[0].decode()
 assert lock_key(path)==result
'''
    subprocess.run([sys.executable, '-B', '-I', '-c', code, str(SOURCE)], check=True, timeout=10)
