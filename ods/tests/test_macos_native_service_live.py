"""Real launchd/native-identity regression; only isolated compiled fixture jobs."""
import hashlib
import json
import os
from pathlib import Path
import pathlib
import shutil
import subprocess
import sys
import tempfile
import threading
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import macos_native_service as module

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="actual Darwin launchd and libproc")
STORE_SOURCE = (ROOT / "extensions/services/dashboard-api/model_stores.py").read_text()


def test_native_replacement_profiles_orphans_and_bystanders():
    root = pathlib.Path(tempfile.mkdtemp(prefix='ods-native-cli-')).resolve()
    install = root / 'Install With Spaces'
    binary = install / 'bin/llama-server'
    binary.parent.mkdir(parents=True)
    source = root / 'fixture.c'
    source.write_text('#include <stdio.h>\n#include <unistd.h>\nint main(void) { puts("ready"); fflush(stdout); sleep(120); return 0; }\n')
    subprocess.run(['/usr/bin/clang', str(source), '-o', str(binary)], check=True, capture_output=True, timeout=45)
    label = 'com.ods.fleet-cli-probe.' + uuid.uuid4().hex
    manager = module.NativeService(install, binary, install / 'data/native.pid', home=root / 'home', label=label)
    manager.domains = lambda: [f'user/{os.getuid()}']
    children = []
    result = {'passed': False, 'source_sha256': hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
              'scope': 'Actual launchd Background jobs and native identity API; isolated compiled fixtures; GUI logged in; installed ODS untouched'}
    try:
        first = manager.start(['--fixture', 'first'])
        before = {str(p): p.read_bytes() for p in (manager.plist, manager.pid_file, manager.journal)}
        assert manager.status()['identity'] == tuple(first['identity'])
        assert before == {str(p): p.read_bytes() for p in (manager.plist, manager.pid_file, manager.journal)}
        result['status_read_only'] = True
        second = manager.start(['--fixture', 'replacement'])
        assert first['identity'] != second['identity'] and not manager.live(first['identity'])
        assert manager.live(second['identity'])
        result['replacement_old_process_gone'] = True
        try:
            manager.rollback(first)
        except module.NativeServiceError:
            pass
        else:
            raise AssertionError('old operation rollback accepted')
        assert manager.live(second['identity'])
        result['old_receipt_preserves_replacement'] = True
        profile_binary = root / 'Registered Runtime/llama-server'
        profile_binary.parent.mkdir()
        shutil.copy2(binary, profile_binary)
        validator = install / 'extensions/services/dashboard-api/model_stores.py'
        validator.parent.mkdir(parents=True)
        validator.write_text(STORE_SOURCE)
        registry = {'schemaVersion': 1, 'stores': [{'id': 'fixture', 'profiles': {'fixture.gguf': {
            'backend': 'metal', 'executable': str(profile_binary), 'contextLength': 4096, 'mtp': False}}}]}
        (install / 'data/model-stores.json').write_text(json.dumps(registry))
        profile = module.NativeService(install, profile_binary, manager.pid_file, home=manager.home, label=label)
        profile.domains = manager.domains
        third = profile.start(['--fixture', 'profile'])
        assert not manager.live(second['identity'])
        assert manager.status()['identity'] == tuple(third['identity'])
        manager.stop()
        assert not profile.live(third['identity'])
        assert manager.status()['running'] is False
        result['default_cli_stops_registered_runtime'] = True
        foreign = root / 'Unrelated/llama-server'
        foreign.parent.mkdir()
        shutil.copy2(binary, foreign)
        bystander = subprocess.Popen([str(foreign), '--model', str(install / 'data/models/fixture.gguf')],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(bystander)
        assert bystander.stdout.readline() == b'ready\n'
        spoof = subprocess.Popen(['/bin/bash', '-c', 'exec -a "$1" /bin/sleep 120', 'fixture', str(binary)])
        children.append(spoof)
        orphan = subprocess.Popen([str(binary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        children.append(orphan)
        assert orphan.stdout.readline() == b'ready\n'
        reaper = threading.Thread(target=orphan.wait, daemon=True)
        reaper.start()
        manager.pid_file.write_text(str(bystander.pid))
        assert manager.status()['identity'][0] == orphan.pid
        manager.stop()
        reaper.join(timeout=5)
        assert orphan.poll() is not None and bystander.poll() is None and spoof.poll() is None
        assert manager.status()['running'] is False
        result.update(orphan_stopped=True, foreign_basename_and_model_spared=True, spoofed_argv_spared=True,
                      stale_pid_never_signalled=True, passed=True)
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
            for stream in (child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        try:
            manager.stop()
            assert not manager.jobs() and not manager.processes()
            shutil.rmtree(root)
            result['fixture_cleanup'] = True
        except Exception as exc:
            result.update(fixture_cleanup=False, recovery_directory=str(root), cleanup_error=str(exc))
    assert result.get('fixture_cleanup'), result
    assert result['passed'], result
