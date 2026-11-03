"""Real Linux child/metadata/signal/reap tests, NOT Source/Darwin admission.

Only invented programs in the fixture world run. The production supervisor
uses its existing Darwin observers by default; these explicit Linux observers
read only the known child tree's proc metadata, never arguments/env/accounts.
"""
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

if sys.platform != 'linux' or not Path('/proc/self/stat').is_file():
    pytest.skip('actual Linux/proc child control; no Darwin claim', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'bin'))
from pixel_connections import custody as source  # noqa: E402


def identity(pid):
    try:
        raw = Path('/proc/' + str(pid) + '/stat').read_text()
        fields = raw[raw.rfind(')') + 2:].split()
        if fields[0] in ('Z', 'X'):
            raise ProcessLookupError()
        status = dict(line.split(':', 1) for line in Path('/proc/' + str(pid) + '/status').read_text().splitlines() if ':' in line)
        uid = [int(value) for value in status['Uid'].split()]
        gid = [int(value) for value in status['Gid'].split()]
        executable = os.readlink('/proc/' + str(pid) + '/exe')
        return (pid, int(fields[19]), 0, uid[1], gid[1], uid[0], gid[0], uid[2], gid[2], executable)
    except FileNotFoundError:
        raise ProcessLookupError() from None


def tree(pid):
    found, queue = [], [pid]
    while queue:
        current = queue.pop()
        try:
            record = identity(current)
            found.append(record[:3])
            children = Path('/proc/' + str(current) + '/task/' + str(current) + '/children').read_text().split()
            queue.extend(int(value) for value in children)
        except ProcessLookupError:
            continue
        assert len(found) <= 64
    return tuple(found)


@pytest.fixture
def launch(tmp_path):
    children = []
    def run(body):
        read, write = os.pipe()
        started = time.monotonic()
        code = ('import os,signal,sys,time\n' + body.replace('READY', 'os.write(' + str(write) + ',b"R")') + '\n')
        process = subprocess.Popen([sys.executable, '-I', '-B', '-c', code],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            pass_fds=(write,), cwd=tmp_path,
            env={'HOME': str(tmp_path), 'TMPDIR': str(tmp_path), 'PATH': '/usr/bin:/bin'})
        children.append(process)
        os.close(write)
        try:
            assert select.select([read], [], [], 5)[0], 'invented child did not become ready'
            assert os.read(read, 1) == b'R', process.stderr.read().decode()
        finally:
            os.close(read)
        return process, started
    yield run
    for process in children:
        if process.poll() is None:
            for row in tree(process.pid):
                if row[0] == process.pid:
                    continue
                try:
                    current = identity(row[0])
                    if current[:3] == row and current[3] == os.getuid():
                        os.kill(row[0], signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill()  # Fixture owns this exact Popen handle.
        process.wait(timeout=3)
        process.stderr.close()


def witness(process, started, **kwargs):
    return source._native_observe_child(process, started=started, uid=os.getuid(), gid=os.getgid(),
        executable=str(Path(sys.executable).resolve()), inspect=kwargs.get('inspect', identity), tree=kwargs.get('tree', tree))


def test_actual_exit_is_reaped_but_not_interpreted_as_provider_success(launch):
    process, started = launch('READY\ntime.sleep(.2)\nsys.exit(7)')
    result = source._native_supervise_child(witness(process, started), role='sync', timeout=2)
    assert (result.disposition, result.returncode, result.reaped, result.known_children_gone) == ('exited', 7, True, True)


def test_actual_term_ignoring_child_is_killed_and_reaped_within_total_budget(launch):
    process, started = launch('signal.signal(signal.SIGTERM,signal.SIG_IGN)\nREADY\ntime.sleep(30)')
    result = source._native_supervise_child(witness(process, started), role='direct', timeout=2)
    assert result.disposition == 'timed-out' and result.returncode == -signal.SIGKILL
    assert result.reaped and result.known_children_gone
    assert time.monotonic() - started < 2.5


def test_total_budget_includes_time_already_spent_before_supervision(launch):
    process, started = launch('signal.signal(signal.SIGTERM,signal.SIG_IGN)\nREADY\ntime.sleep(30)')
    bound = witness(process, started)
    time.sleep(.8)
    result = source._native_supervise_child(bound, role='direct', timeout=2)
    assert result.disposition == 'timed-out' and result.reaped
    assert time.monotonic() - started < 2.2


def test_actual_cancellation_stops_only_owned_child_and_never_repeats_it(launch):
    process, started = launch('READY\ntime.sleep(30)')
    unrelated, _ = launch('READY\ntime.sleep(30)')
    cancel = threading.Event()
    timer = threading.Timer(.15, cancel.set)
    timer.start()
    try:
        result = source._native_supervise_child(witness(process, started), role='action', timeout=2, cancel=cancel)
    finally:
        timer.join(timeout=1)
    assert result.disposition == 'cancelled' and result.reaped and result.known_children_gone, (result, time.monotonic() - started)
    assert unrelated.poll() is None  # Same UID is NOT signal authority.
    assert process.returncode == -signal.SIGTERM


@pytest.mark.parametrize('late', [False, True])
def test_actual_known_descendant_is_stopped_without_signalling_same_uid_sibling(launch, tmp_path, late):
    delay = 'time.sleep(.2)' if late else ''
    ready = 'READY' if late else ''
    after = '' if late else 'READY'
    body = f'''{ready}
{delay}
child=os.fork()
if child==0:
    time.sleep(30)
    os._exit(0)
def finish(_sig,_frame):
    os.waitpid(child,0)
    sys.exit(0)
signal.signal(signal.SIGTERM,finish)
{after}
time.sleep(30)'''
    process, started = launch(body)
    unrelated, _ = launch('READY\ntime.sleep(30)')
    observed = []
    def real_tree(pid):
        rows = tree(pid)
        observed.extend(rows)
        return rows
    result = source._native_supervise_child(witness(process, started, tree=real_tree), role='exchange', timeout=2)
    assert len({record[0] for record in observed}) == 2
    assert result.disposition == 'timed-out' and result.reaped and result.known_children_gone
    assert unrelated.poll() is None


def test_changed_identity_produces_unknown_and_does_not_signal_another_actual_process(launch):
    process, started = launch('READY\ntime.sleep(30)')
    unrelated, _ = launch('READY\ntime.sleep(30)')
    bound = witness(process, started)
    # Fault injection uses a real DIFFERENT identity, never a fabricated match.
    changed = source._NativeChildWitness(bound.process, bound.started, bound.root, bound.identities,
        lambda pid: identity(unrelated.pid), bound.tree, bound.seal)
    result = source._native_supervise_child(changed, role='revoke', timeout=.4)
    assert result.disposition == 'unknown' and not result.known_children_gone
    assert process.poll() is None and unrelated.poll() is None


def test_observation_failure_retains_unknown_even_when_exact_child_cleanup_succeeds(launch):
    process, started = launch('READY\ntime.sleep(30)')
    bound = witness(process, started)
    def unavailable(_pid):
        raise OSError('invented process metadata unavailable')
    changed = source._NativeChildWitness(bound.process, bound.started, bound.root, bound.identities,
        bound.inspect, unavailable, bound.seal)
    result = source._native_supervise_child(changed, role='reconcile', timeout=2)
    assert result.disposition == 'unknown' and result.reaped and result.known_children_gone


def test_nonowned_pid_shape_is_not_a_child_handle():
    with pytest.raises(source.CustodyError, match='child-unbound'):
        source._native_observe_child(SimpleNamespace(pid=os.getpid()), started=time.monotonic(),
            uid=os.getuid(), gid=os.getgid(), executable=str(Path(sys.executable).resolve()))
