"""Actual Root journals, owned Linux children and launchctl protocol simulation.

No native service, account, provider, SDK or model is exercised. Darwin metadata
and launchd are explicit OS-driver substitutions, not physical Mac evidence.
"""
# ruff: noqa: E402,F401,F811
import errno
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

if sys.platform != 'linux':
    pytest.skip('Linux/procfs control driver, not physical Darwin', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_native_source_generation import generation, SPECS
from test_native_source_custody import native
from test_connection_deployment_paths import deployment
from test_native_source_runtime_publication import built, source, package
from test_native_source_publication import material, publication, helper
from pixel_connections import custody as c, native_executor as ex
from pixel_access_bridge import AccessError
import pixel_access_bridge as bridge
import pixel_macos_custody as mac


def proc(pid):
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        fields = raw[raw.rfind(')') + 2:].split()
        if fields[0] in ('Z', 'X'):
            raise FileNotFoundError()
        return int(fields[1]), int(fields[19]), 0
    except (FileNotFoundError, ProcessLookupError):
        raise ex.ProcessIdentityError('gateway-process-unavailable', errno=errno.ESRCH) from None


class Launchd:
    def __init__(self, n):
        self.n, self.jobs, self.children, self.sent, self.fail = n, {}, [], [], None

    def frame(self, label):
        row = self.jobs[label]
        process = row['process']
        if process is not None and process.poll() is not None:
            row['exit'] = process.returncode if process.returncode >= 0 else 2
            row['process'] = None
        doc = row['document']
        lines = ['system/' + label + ' = {', '\tpath = ' + str(row['path']),
            '\tprogram = ' + doc['ProgramArguments'][0], '\tworking directory = ' + doc['WorkingDirectory'],
            '\tstdout path = ' + doc['StandardOutPath'], '\tstderr path = ' + doc['StandardErrorPath'],
            '\tstate = ' + ('running' if row['process'] is not None else 'not running'),
            '\truns = ' + str(row['runs']), '\targuments = {']
        lines += ['\t\t' + arg for arg in doc['ProgramArguments']]
        lines += ['\t}', '\tenvironment = {', '\t\tXPC_SERVICE_NAME => ' + label, '\t}']
        if row['process'] is not None:
            lines.append('\tpid = ' + str(row['process'].pid))
        if row['exit'] is not None:
            lines.append('\tlast exit code = ' + str(row['exit']))
        return '\n'.join([*lines, '}']) + '\n'

    def release(self, label):
        row = self.jobs[label]
        os.write(row['release'], b'G')
        os.close(row['release'])
        row['release'] = None
        row['process'].wait(timeout=5)

    def __call__(self, args, timeout=20):
        self.sent.append(list(args))
        if args == ['/bin/launchctl', 'print', 'system']:
            return 'system = {\n services = {\n' + '\n'.join(' ' + label + ' = {}' for label in self.jobs) + '\n }\n}\n'
        if args == ['/bin/ps', '-axo', 'pid=,uid=']:
            pids = []
            for process in self.children:
                if process.poll() is None:
                    pids.append(f'{process.pid} {self.n.identity["id"]}')
            return '\n'.join(['1 0', *pids]) + '\n'
        if args == ['/usr/sbin/sysctl', '-n', 'kern.bootsessionuuid']:
            return '01234567-89AB-4DEF-8123-0123456789AB\n'
        if args[:2] == ['/bin/launchctl', 'print']:
            label = args[2].removeprefix('system/')
            if label not in self.jobs:
                raise AccessError('host-command-failed', returncode=113)
            return self.frame(label)
        if args[:3] == ['/bin/launchctl', 'bootstrap', 'system']:
            path = Path(args[3])
            label = path.stem
            assert label not in self.jobs
            self.jobs[label] = {'path': path, 'document': plistlib.loads(path.read_bytes()),
                'runs': 0, 'exit': None, 'process': None, 'release': None}
            return ''
        if args[:2] == ['/bin/launchctl', 'kickstart']:
            assert len(args) == 3 and '-k' not in args
            label = args[2].removeprefix('system/')
            row = self.jobs[label]
            assert row['runs'] == 0
            if self.fail == 'before-start':
                self.fail = None
                raise AccessError('host-command-failed')
            read, write = os.pipe()
            try:
                process = subprocess.Popen([str(self.n.built.python), '-I', '-S', '-c',
                    f'import os; os.read({read},1)'], stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, pass_fds=(read,))
            finally:
                os.close(read)
            row.update(runs=1, process=process, release=write)
            self.children.append(process)
            if self.fail == 'after-start':
                self.fail = None
                raise AccessError('host-command-failed')
            return ''
        if args[:2] == ['/bin/launchctl', 'bootout']:
            label = args[2].removeprefix('system/')
            row = self.jobs.pop(label)
            process = row['process']
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            if row['release'] is not None:
                os.close(row['release'])
            if self.fail == 'after-stop':
                self.fail = None
                raise AccessError('host-command-failed')
            return ''
        raise AssertionError('unbound/native service command: ' + repr(args))


@pytest.fixture(autouse=True)
def qualification_operation(deployment):
    with deployment.bridge._native_runtime_operation():
        yield


@pytest.fixture
def executor(generation, built, monkeypatch):
    n = generation
    n.built = built
    helper.publish_disabled_generation(n.selected, SPECS[:2])
    n.now = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW)
    monkeypatch.setattr(c, '_native_clock_ns', lambda: n.now)
    monkeypatch.setattr(c, '_native_boot_uuid', lambda: '01234567-89AB-4DEF-8123-0123456789AB')
    monkeypatch.setattr(bridge, '_native_source_clock_ns', lambda: n.now)
    monkeypatch.setattr(bridge, 'time', SimpleNamespace(monotonic=lambda: 1000.0, sleep=time.sleep))
    monkeypatch.setattr(ex, 'protected_directory', mac.protected_directory)
    # Existing OS metadata simulation must discard stale ownership only for an
    # inode actually newly allocated by Linux, including new directory inodes.
    original_open, original_mkdir = os.open, os.mkdir
    def clear(fd):
        if Path(os.readlink(f'/proc/self/fd/{fd}')).is_relative_to(n.world):
            info = n.real_fstat(fd)
            n.owners.pop((info.st_dev, info.st_ino), None)
            n.acl.acls.pop((info.st_dev, info.st_ino), None)
    def opened(path, flags, *args, **kwargs):
        fd = original_open(path, flags, *args, **kwargs)
        if flags & os.O_CREAT and flags & os.O_EXCL:
            clear(fd)
        return fd
    def mkdir(path, *args, **kwargs):
        original_mkdir(path, *args, **kwargs)
        fd = original_open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            **({'dir_fd': kwargs['dir_fd']} if 'dir_fd' in kwargs else {}))
        try:
            clear(fd)
        finally:
            os.close(fd)
    monkeypatch.setattr(os, 'open', opened)
    monkeypatch.setattr(os, 'mkdir', mkdir)
    # Full production bundle/authority verification remains real. Only the
    # relocated protected-helper import uses the existing Root metadata driver.
    def publisher(selection):
        helper._material(selection)
        return helper
    monkeypatch.setattr(ex, '_publisher', publisher)
    driver = Launchd(n)
    monkeypatch.setattr(n.f.bridge, 'command', driver)
    def identity(pid, *, uid, gid, executable):
        assert uid == gid == n.identity['id']
        _parent, sec, usec = proc(pid)
        actual = os.readlink(f'/proc/{pid}/exe')
        assert actual == executable == str(built.python)
        return (pid, sec, usec, uid, gid, uid, gid, uid, gid, executable)
    def tree(pid):
        parent, sec, usec = proc(pid)
        rows = [(pid, sec, usec)]
        for process in driver.children:
            if process.pid != pid and process.poll() is None:
                ppid, s, u = proc(process.pid)
                if ppid == pid:
                    rows.append((process.pid, s, u))
        return tuple(sorted(rows))
    monkeypatch.setattr(ex, 'process_identity', identity)
    monkeypatch.setattr(ex, 'process_tree_snapshot', tree)
    monkeypatch.setattr(ex, 'process_birth', lambda pid: proc(pid)[1:])
    n.driver = driver
    with n.f.bridge.bounded(120):
        n.executor = ex.NativeSourceExecutor(n.selected)
        try:
            yield n
        finally:
            for row in driver.jobs.values():
                if row['release'] is not None:
                    os.close(row['release'])
            for process in driver.children:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


def test_actual_children_start_once_stop_one_sibling_and_repeat_with_fresh_nonce(executor, monkeypatch):
    n = executor
    sync = n.executor.start('sync')
    direct = n.executor.start('direct')
    assert sync['phase'] == direct['phase'] == 'observed'
    assert int(sync['invocation']['admissionDeadlineNs']) <= n.now + 120 * 10**9
    original = sync['nonce']
    # Natural completion, fixed-label unload, and exact PID/birth absence.
    n.driver.release(c.NATIVE_LABEL + 'sync')
    sync = n.executor.observe('sync')
    assert sync['phase'] == 'stopped' and sync['exitCode'] == 0 and not sync['uncertain']
    assert n.driver.jobs[c.NATIVE_LABEL + 'direct']['process'].poll() is None
    ex.verify_stopped(n.selected, 'sync', None, original)
    snapshot, observations = ex.process_tree_snapshot, []
    def sibling_drift(pid):
        observations.append(pid)
        rows = snapshot(pid)
        return rows if len(observations) == 1 else (*rows, (2147483646, 1, 0))
    monkeypatch.setattr(ex, 'process_tree_snapshot', sibling_drift)
    with pytest.raises(c.CustodyError, match='sibling-changed'):
        ex.verify_stopped(n.selected, 'sync', None, original)
    monkeypatch.setattr(ex, 'process_tree_snapshot', snapshot)
    rearm, captured = helper.rearm_active_slot, []
    def lost_rearm(*args, **kwargs):
        captured.append(rearm(*args, **kwargs))
        raise AccessError('host-command-failed')
    monkeypatch.setattr(helper, 'rearm_active_slot', lost_rearm)
    with pytest.raises(AccessError):
        n.executor.start('sync')
    monkeypatch.setattr(helper, 'rearm_active_slot', rearm)
    next_run = n.executor.start('sync')
    assert next_run['invocation'] == captured[0]['invocation']
    assert next_run['phase'] == 'observed' and next_run['nonce'] != original
    assert n.executor.start('sync')['nonce'] == next_run['nonce']
    assert sum(args[:2] == ['/bin/launchctl', 'kickstart'] and args[-1].endswith('sync') for args in n.driver.sent) == 2
    assert not any('-k' in args for args in n.driver.sent)
    for role in ('sync', 'direct'):
        n.driver.release(c.NATIVE_LABEL + role)
    # Both wrappers can finish before either Root observation. Neither leaves
    # the other's stop proof waiting for a live sibling PID indefinitely.
    for role in ('sync', 'direct'):
        n.executor.observe(role)
    action_id = 'calendar-1893456789012-12abcdef'
    action = n.executor.start('action', action_id)
    n.driver.release(c.NATIVE_LABEL + 'action.' + action_id)
    action = n.executor.observe('action', action_id)
    before = len([args for args in n.driver.sent if args[:2] == ['/bin/launchctl', 'kickstart']])
    assert n.executor.start('action', action_id)['nonce'] == action['nonce']
    assert len([args for args in n.driver.sent if args[:2] == ['/bin/launchctl', 'kickstart']]) == before


def test_lost_start_and_stop_replies_never_replay_and_unknown_start_holds(executor, monkeypatch):
    n = executor
    admit, captured = helper.admit_active_slot, []
    def lost_admission(*args, **kwargs):
        captured.append(admit(*args, **kwargs))
        raise AccessError('host-command-failed')
    monkeypatch.setattr(helper, 'admit_active_slot', lost_admission)
    with pytest.raises(AccessError):
        n.executor.start('sync')
    pending = n.executor._read()[0]['admission']
    assert int(pending['ceilingNs']) == int(captured[0]['invocation']['issuedNs']) + 120 * 10**9
    monkeypatch.setattr(helper, 'admit_active_slot', admit)
    n.now += 10**9
    n.driver.fail = 'after-start'
    first = n.executor.start('sync')
    assert first['phase'] == 'observed'
    assert first['invocation'] == captured[0]['invocation']
    assert n.executor.start('sync')['nonce'] == first['nonce']
    assert sum(args[:2] == ['/bin/launchctl', 'kickstart'] for args in n.driver.sent) == 1
    n.driver.fail = 'after-stop'
    transition = helper.transition_active_slot
    def lost_retirement(*args, **kwargs):
        if kwargs['phase'] == 'retired':
            raise AccessError('host-command-failed')
        return transition(*args, **kwargs)
    monkeypatch.setattr(helper, 'transition_active_slot', lost_retirement)
    with pytest.raises(AccessError):
        n.executor.stop('sync')
    assert n.executor._read()[0]['jobs'][c.NATIVE_LABEL + 'sync']['phase'] == 'stopped'
    bootouts = sum(args[:2] == ['/bin/launchctl', 'bootout'] for args in n.driver.sent)
    monkeypatch.setattr(helper, 'transition_active_slot', transition)
    stopped = n.executor.observe('sync')
    assert sum(args[:2] == ['/bin/launchctl', 'bootout'] for args in n.driver.sent) == bootouts
    assert stopped['phase'] == 'stopped' and stopped['uncertain']
    ex.verify_stopped(n.selected, 'sync', None, first['nonce'])
    n.driver.fail = 'before-start'
    pending = n.executor.start('direct')
    assert pending['phase'] == 'start-intent'
    before = sum(args[:2] == ['/bin/launchctl', 'kickstart'] for args in n.driver.sent)
    n.now += 121 * 10**9
    assert n.executor.observe('direct')['phase'] == 'unknown'
    assert n.executor.start('direct')['phase'] == 'unknown'
    assert sum(args[:2] == ['/bin/launchctl', 'kickstart'] for args in n.driver.sent) == before


def test_triggers_are_explicit_metadata_only_and_failed_proof_cannot_rearm(executor, monkeypatch):
    n = executor
    assert not n.executor._read()[0]['triggers']['sync']
    n.executor.trigger('sync', enabled=True)
    n.executor.trigger('direct', enabled=True)
    n.executor.tick()
    assert not n.driver.jobs
    n.now += 61 * 10**9
    n.executor.tick()
    assert c.NATIVE_LABEL + 'sync' in n.driver.jobs
    n.executor.trigger('sync', enabled=False)
    n.driver.release(c.NATIVE_LABEL + 'sync')
    job = n.executor.observe('sync')
    assert job['phase'] == 'stopped'
    original = ex.process_birth
    monkeypatch.setattr(ex, 'process_birth', lambda pid: (job['tree'][0][1], job['tree'][0][2]))
    with pytest.raises(c.CustodyError, match='descendant-still-live'):
        ex.verify_stopped(n.selected, 'sync', None, job['nonce'])
    monkeypatch.setattr(ex, 'process_birth', lambda pid: (job['tree'][0][1] + 1, job['tree'][0][2]))
    ex.verify_stopped(n.selected, 'sync', None, job['nonce'])  # Reused PID grants no signal authority.
    monkeypatch.setattr(ex, 'process_birth', original)
    proposal = c.NATIVE_STATE / 'proposals/calendar-1893456789012-12abcdef.json'
    parent = os.open(proposal.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        child = os.open(proposal.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        try:
            info = os.fstat(parent)
            os.fchown(child, info.st_uid, info.st_gid)
            os.write(child, b'{"invented":"never interpreted by trigger"}')
            os.fsync(child)
        finally:
            os.close(child)
    finally:
        os.close(parent)
    n.executor.tick()
    assert c.NATIVE_LABEL + 'direct' in n.driver.jobs
    assert not any('-k' in args for args in n.driver.sent)
