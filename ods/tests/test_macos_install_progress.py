"""The macOS progress command observes a worker; it never restarts one."""
import importlib.util
import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_progress',
    ROOT / 'installers/macos/lib/pixel-native-progress.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.fixture(autouse=True)
def portable_read_flags(monkeypatch):
    # Windows exercises numeric parsing; POSIX CI also exercises link/FIFO
    # rejection with the real platform flags and owner checks.
    if os.name == 'nt':
        monkeypatch.setattr(module.os, 'O_NOFOLLOW', 0, raising=False)
        monkeypatch.setattr(module.os, 'O_NONBLOCK', 0, raising=False)
        monkeypatch.setattr(module.os, 'getuid', lambda: 0, raising=False)


def write_status(root, **changes):
    path = root / 'data/bootstrap-status.json'
    path.parent.mkdir(exist_ok=True)
    record = dict(status='downloading', model='model.gguf', percent=12.5,
        bytesDownloaded=125, bytesTotal=1000, speedBytesPerSec=10,
        eta='do-not-publish-private-error', updatedAt='2026-10-08T12:00:00Z')
    record.update(changes)
    path.write_text(json.dumps(record))
    return path


def test_progress_reports_metrics_without_error_or_extra_private_fields(tmp_path):
    path = write_status(tmp_path, token='private-token')
    before = path.read_bytes()
    result = module.progress(tmp_path)
    assert result == dict(status='downloading', percent=12.5, bytesDownloaded=125,
        bytesTotal=1000, speedBytesPerSec=10, updatedAt='2026-10-08T12:00:00Z')
    assert path.read_bytes() == before
    assert 'private' not in json.dumps(result)


@pytest.mark.parametrize('changes', [dict(status=[]), dict(status='unexpected'),
    dict(percent=101), dict(percent=True), dict(percent=float('nan')),
    dict(bytesDownloaded=-1), dict(speedBytesPerSec='private-token'), dict(bytesTotal=10 ** 400)])
def test_progress_rejects_malformed_metrics(tmp_path, changes):
    write_status(tmp_path, **changes)
    with pytest.raises(ValueError, match='invalid-model-progress'):
        module.progress(tmp_path)


def test_missing_status_is_not_success(tmp_path):
    assert module.progress(tmp_path) == {'status': 'not-recorded'}


def test_oversized_progress_is_rejected(tmp_path):
    path = write_status(tmp_path)
    path.write_text(' ' * 65537)
    with pytest.raises(ValueError, match='model-progress-too-large'):
        module.progress(tmp_path)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX file custody')
@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'hardlink'])
def test_progress_rejects_unsafe_files_without_waiting(tmp_path, kind):
    path = write_status(tmp_path)
    other = tmp_path / 'other.json'
    path.rename(other)
    if kind == 'symlink':
        path.symlink_to(other)
    elif kind == 'fifo':
        os.mkfifo(path)
    else:
        os.link(other, path)
    with pytest.raises((OSError, ValueError)):
        module.progress(tmp_path)


@pytest.mark.skipif(os.name != 'posix', reason='Atomic replacement of an opened POSIX file')
@pytest.mark.parametrize('replacement', ['ordinary', 'hardlink', 'missing', 'continuous'])
def test_progress_reopens_atomically_replaced_record_without_relaxing_custody(tmp_path, monkeypatch, replacement):
    path = write_status(tmp_path, status='failed')
    original_open = os.open
    opened = []

    def replacing_open(filename, flags, *args, **kwargs):
        descriptor = original_open(filename, flags, *args, **kwargs)
        if Path(filename) == path:
            opened.append(descriptor)
            if len(opened) == 1 or replacement == 'continuous':
                if replacement == 'missing':
                    path.unlink()
                else:
                    temporary = tmp_path / 'next-status.json'
                    temporary.write_text(json.dumps(dict(status='downloading', percent=25)))
                    if replacement == 'hardlink':
                        path.unlink()
                        os.link(temporary, path)
                    else:
                        os.replace(temporary, path)
                # This is the actual inode state caused by an atomic publisher,
                # not a fabricated fstat result or a timing-dependent race.
                assert os.fstat(descriptor).st_nlink == 0
        return descriptor

    monkeypatch.setattr(module.os, 'open', replacing_open)
    if replacement == 'ordinary':
        assert module.progress(tmp_path)['status'] == 'downloading'
        assert len(opened) == 2
    elif replacement == 'missing':
        assert module.progress(tmp_path) == {'status': 'not-recorded'}
        assert len(opened) == 1
    else:
        expected = 'unsafe-model-progress' if replacement == 'hardlink' else 'model-progress-changing'
        with pytest.raises(ValueError, match=expected):
            module.progress(tmp_path)
        assert len(opened) == (2 if replacement == 'hardlink' else 3)
    for descriptor in opened:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_watch_follows_download_verification_activation_then_completion(capsys):
    records = iter([dict(status=state, percent=percent) for state, percent in
        [('starting', None), ('downloading', 25), ('verifying', 100),
         ('swapping', 100), ('complete', 100)]])
    pauses = []
    liveness = iter([True, True, True, True, False])
    result = module.watch('/installed', observe=lambda _: next(records),
        running=lambda _: next(liveness), pause=pauses.append)
    assert result == 0
    assert pauses == [5, 5, 5, 5]
    output = capsys.readouterr().out
    assert 'Verifying the download' in output
    assert 'Activating the full model' in output
    assert 'Verify the active model and a response in Portal' in output
    assert 'installation complete' not in output.lower()


def test_watch_reports_stopped_worker_instead_of_waiting_forever(capsys):
    def must_not_sleep(_):
        pytest.fail('No worker remains to wait for')
    assert module.watch('/installed', observe=lambda _: dict(status='downloading', percent=25),
        running=lambda _: False, pause=must_not_sleep) == 1
    assert 'No matching model worker is running' in capsys.readouterr().out


@pytest.mark.parametrize('old_status', ['failed', 'complete'])
def test_watch_waits_for_live_worker_despite_old_terminal_status(old_status, capsys):
    records = iter([dict(status=state) for state in
                    (old_status, 'starting', 'complete', 'complete')])
    liveness = iter([True, True, True, False])
    pauses = []
    assert module.watch('/installed', observe=lambda _: next(records),
        running=lambda _: next(liveness), pause=pauses.append) == 0
    assert pauses == [5, 5, 5]
    assert 'waiting for its final status' in capsys.readouterr().out


def test_watch_reports_failure_without_retrying(capsys):
    def must_not_probe(_):
        pytest.fail('A recorded failure should stop the watcher')
    assert module.watch('/installed', observe=lambda _: dict(status='failed'),
        running=lambda _: False, pause=must_not_probe) == 1
    assert 'logs/model-upgrade.log' in capsys.readouterr().out.replace('\\', '/')


def test_ctrl_c_leaves_the_detached_worker_running(capsys):
    def interrupt(_):
        raise KeyboardInterrupt()
    assert module.watch('/installed', observe=lambda _: dict(status='downloading'),
        running=lambda _: True, pause=interrupt) == 0
    assert 'worker were left running' in capsys.readouterr().out
