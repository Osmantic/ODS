"""The progress display follows a real detached worker across stale records.

No Docker or protected services are used. This test requires the production
POSIX custody flags and atomic replacement of an open status file. Windows
does not provide those filesystem semantics; portable watcher behavior is
covered separately by the progress unit tests.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('integration_progress',
    ROOT / 'installers/macos/lib/pixel-native-progress.py')
progress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(progress)


WORKER = r'''
import json, os, pathlib, sys, time
root = pathlib.Path(sys.argv[1])
deadline = time.monotonic() + 10
def wait(name):
    while not (root / name).exists():
        if time.monotonic() >= deadline:
            raise SystemExit(8)
        time.sleep(0.005)
wait('begin')
for status in ('starting', 'downloading', 'verifying', 'swapping', 'complete'):
    temporary = root / 'data/next-status.json'
    temporary.write_text(json.dumps({'status': status, 'percent': 100 if status == 'complete' else 25}))
    os.replace(temporary, root / 'data/bootstrap-status.json')
    wait('observed-' + status)
'''


@pytest.mark.skipif(os.name != 'posix', reason='Requires production POSIX file custody and atomic replacement')
@pytest.mark.parametrize('old_status', ['failed', 'complete'])
def test_live_worker_is_followed_instead_of_trusting_previous_terminal_record(
    tmp_path, capsys, old_status,
):
    (tmp_path / 'data').mkdir()
    status_path = tmp_path / 'data/bootstrap-status.json'
    status_path.write_text(json.dumps({'status': old_status, 'percent': 100}))
    worker = subprocess.Popen([sys.executable, '-I', '-c', WORKER, str(tmp_path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True)
    seen = []
    observed_live_complete = False
    deadline = time.monotonic() + 10

    def observe(root):
        record = progress.progress(root)
        state = record['status']
        seen.append(state)
        if state in ('starting', 'downloading', 'verifying', 'swapping'):
            (tmp_path / ('observed-' + state)).touch()
        return record

    def pause(_seconds):
        nonlocal observed_live_complete
        assert time.monotonic() < deadline, 'The watcher did not finish after the worker exited'
        if not (tmp_path / 'begin').exists():
            # This can only be reached if an old failed/complete record was
            # not accepted as terminal while a new worker remains alive.
            assert worker.poll() is None
            (tmp_path / 'begin').touch()
        elif seen[-1] == 'complete' and (tmp_path / 'observed-swapping').exists():
            observed_live_complete = True
            (tmp_path / 'observed-complete').touch()
        time.sleep(0.005)

    try:
        result = progress.watch(tmp_path, observe=observe,
            running=lambda root: worker.poll() is None, pause=pause)
        if worker.poll() is not None and worker.returncode != 0:
            pytest.fail(f'Fixture worker exited {worker.returncode}: {worker.stderr.read().decode(errors="replace")}')
        assert result == 0
        assert observed_live_complete
        assert worker.wait(timeout=2) == 0
        assert {'starting', 'downloading', 'verifying', 'swapping', 'complete'} <= set(seen)
        assert json.loads(status_path.read_text())['status'] == 'complete'
        output = capsys.readouterr().out
        assert 'Verifying the download' in output
        assert 'Activating the full model' in output
        assert 'installation complete' not in output.lower()
    except BaseException:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=2)
        diagnostic = worker.stderr.read().decode(errors='replace').strip()
        if diagnostic:
            print(f'Fixture worker stderr: {diagnostic}', file=sys.stderr)
        raise
    finally:
        if worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=2)
        worker.stderr.close()
