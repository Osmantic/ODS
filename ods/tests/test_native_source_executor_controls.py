"""Closed launchd observation grammar and disabled worker controls."""
# ruff: noqa: E402
from pathlib import Path
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_connections import native_executor as ex
from pixel_connections.custody import CustodyError


@pytest.fixture
def observation():
    label = 'com.ods.pixel-native-source-sync'
    path = Path('/Library/LaunchDaemons/' + label + '.plist')
    document = {'Label': label, 'ProgramArguments': ['/usr/bin/env', '-i', '/trusted/python'],
        'WorkingDirectory': '/private/var/lib/ods-source', 'StandardOutPath': '/private/var/log/source.stdout',
        'StandardErrorPath': '/private/var/log/source.stderr'}
    frame = ('system/' + label + ' = {\n\tpath = ' + str(path)
        + '\n\tprogram = /usr/bin/env\n\tworking directory = /private/var/lib/ods-source'
        + '\n\tstdout path = /private/var/log/source.stdout\n\tstderr path = /private/var/log/source.stderr'
        + '\n\targuments = {\n\t\t/usr/bin/env\n\t\t-i\n\t\t/trusted/python\n\t}'
        + '\n\tenvironment = {\n\t\tXPC_SERVICE_NAME => ' + label + '\n\t}'
        + '\n\tstate = running\n\tpid = 10\n\truns = 1\n\tlast exit code = 0\n}\n')
    def observe(text):
        class Bridge:
            def command(self, arguments, timeout):
                assert arguments == ['/bin/launchctl', 'print', 'system/' + label]
                return text
        executor = object.__new__(ex.NativeSourceExecutor)
        executor.bridge = Bridge()
        return executor._loaded(label, document, path)
    return frame, observe


@pytest.mark.parametrize('started', [False, True])
def test_new_launchd_job_has_no_previous_exit(observation, started):
    frame, observe = observation
    frame = frame.replace('last exit code = 0', 'last exit code = (never exited)')
    if not started:
        frame = frame.replace('state = running\n\tpid = 10\n\truns = 1', 'state = not running\n\truns = 0')
    assert observe(frame) == {'pid': 10 if started else None, 'runs': int(started), 'exitCode': None}


@pytest.mark.parametrize('change', ['duplicate-runs', 'bad-runs', 'overflow-runs', 'bad-pid',
    'overflow-pid', 'duplicate-pid', 'missing-pid', 'unexpected-state', 'bad-exit', 'overflow-exit',
    'bad-never-exited', 'duplicate-exit'])
def test_unknown_or_ambiguous_loaded_frame_cannot_authorize_control(observation, change):
    frame, observe = observation
    mutations = {'duplicate-runs': ('\truns = 1', '\truns = 1\n\truns = 1'),
        'bad-runs': ('\truns = 1', '\truns = 1garbage'),
        'overflow-runs': ('\truns = 1', '\truns = 2147483648'),
        'bad-pid': ('\tpid = 10', '\tpid = -10'),
        'overflow-pid': ('\tpid = 10', '\tpid = 2147483648'),
        'duplicate-pid': ('\tpid = 10', '\tpid = 10\n\tpid = 10'),
        'missing-pid': ('\tpid = 10\n', ''),
        'unexpected-state': ('\tstate = running', '\tstate = throttled'),
        'bad-exit': ('\tlast exit code = 0', '\tlast exit code = 0garbage'),
        'overflow-exit': ('\tlast exit code = 0', '\tlast exit code = 256'),
        'bad-never-exited': ('\tlast exit code = 0', '\tlast exit code = (never exited) extra'),
        'duplicate-exit': ('\tlast exit code = 0', '\tlast exit code = (never exited)\n\tlast exit code = 0')}
    assert observe(frame) == {'pid': 10, 'runs': 1, 'exitCode': 0}
    with pytest.raises(CustodyError, match='observation-unavailable'):
        observe(frame.replace(*mutations[change]))


def test_daemon_without_execution_state_does_not_discover_or_provision(tmp_path, monkeypatch):
    monkeypatch.setattr(ex.c, 'NATIVE_META', tmp_path)
    calls, stop = [], threading.Event()
    worker = threading.Thread(target=ex.trigger_loop, args=(lambda: calls.append(True), stop))
    worker.start()
    try:
        assert not stop.wait(1.2)
    finally:
        stop.set()
        worker.join(timeout=5)
    assert not worker.is_alive() and not calls and not list(tmp_path.iterdir())
