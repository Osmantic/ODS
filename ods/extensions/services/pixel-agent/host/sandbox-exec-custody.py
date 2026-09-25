"""Per-exec Docker child custody. Runs only inside the existing sandbox UID.

The relay preserves the original shell's exit and streams. Its child subreaper
owns descendants until the SDK's existing finalize hook releases or drains it.
Successful release deliberately preserves ordinary detached-shell semantics.
"""
import ctypes
import hashlib
import json
import os
import signal
import select
import stat
import subprocess
import sys
import time

DIRECTORY_FD = None
DIRECTORY_ID = None


def check_directory(folder):
    info = os.lstat(folder)
    if not stat.S_ISDIR(info.st_mode) or [info.st_dev, info.st_ino] != DIRECTORY_ID:
        raise RuntimeError('exec custody directory identity changed')


def identity(pid):
    value = open('/proc/%d/stat' % pid).read()
    fields = value[value.rindex(')') + 2:].split()
    return {'pid': pid, 'parent': int(fields[1]), 'startTicks': fields[19],
            'state': fields[0]}


def put(folder, name, value):
    check_directory(folder)
    data = json.dumps(value, sort_keys=True).encode()
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=DIRECTORY_FD)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    check_directory(folder)


def reap():
    while True:
        try:
            if os.waitpid(-1, os.WNOHANG)[0] == 0:
                return
        except ChildProcessError:
            return


def descendants(root=None):
    rows = {}
    for name in os.listdir('/proc'):
        if name.isdigit():
            try:
                row = identity(int(name))
                rows[row['pid']] = row
            except (FileNotFoundError, ProcessLookupError):
                pass
    root = os.getpid() if root is None else root
    owned = {root}
    while True:
        more = {pid for pid, row in rows.items() if row['parent'] in owned}
        if more <= owned:
            return [row for pid, row in rows.items() if pid in owned and pid != root]
        owned.update(more)


def send_exact(row, sig):
    try:
        fd = os.pidfd_open(row['pid'])
    except ProcessLookupError:
        return
    try:
        if identity(row['pid'])['startTicks'] != row['startTicks']:
            raise RuntimeError('PID identity changed')
        signal.pidfd_send_signal(fd, sig)
    except (FileNotFoundError, ProcessLookupError):
        pass
    finally:
        os.close(fd)


def drain():
    # A subreaper keeps double-fork/setsid descendants in this ancestry.
    # Stop before killing so a late-spawning descendant cannot escape a sweep.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        reap()
        rows = descendants()
        if not rows:
            return True
        for row in rows:
            send_exact(row, signal.SIGSTOP)
        for row in descendants():
            send_exact(row, signal.SIGKILL)
        time.sleep(0.01)
    return False


def hold_custody():
    # Broken metadata must not turn the subreaper into an orphaning mechanism.
    # Retain its children until the owner settles this explicitly uncertain run.
    while True:
        time.sleep(1)


def launch(folder, nonce, command, cancel_marker):
    read_fd, write_fd = os.pipe()
    supervisor = os.fork()
    if supervisor:
        # The command receives terminal signals through its unchanged process
        # group. The relay must remain alive long enough to preserve its exit.
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        os.close(write_fd)
        data = b''
        while True:
            chunk = os.read(read_fd, 32)
            if not chunk:
                break
            data += chunk
        os.close(read_fd)
        return int(data.decode()) if data else 125
    os.close(read_fd)
    if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
        os._exit(125)
    signal.signal(signal.SIGUSR1, lambda *_: None)
    admitted = [False]
    signal.signal(signal.SIGUSR2, lambda *_: admitted.__setitem__(0, True))
    interrupted = [False]
    def interrupt(*_):
        interrupted[0] = True
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupt)
    binding = {**identity(os.getpid()), 'nonce': nonce,
               'commandSha256': hashlib.sha256(command.encode()).hexdigest()}
    put(folder, 'owner.json', binding)  # Diagnostic only; never settlement authority.
    os.write(1, ('ODS_EXEC_CUSTODY_V1 %s %d %s\n' % (nonce, binding['pid'], binding['startTicks'])).encode())
    while not admitted[0]:
        time.sleep(.01)
    child = subprocess.Popen(['/bin/sh', '-lc', command])
    # Only the original command retains the external streams after spawn.
    for fd in (0, 1, 2):
        os.close(fd)
    sent = False
    marker_seen = None
    while True:
        code = child.poll()
        if code is not None and not sent:
            os.write(write_fd, str(code if code >= 0 else 128 - code).encode())
            os.close(write_fd)
            sent = True
        if cancel_marker:
            try:
                marker = os.lstat(cancel_marker)
                if not stat.S_ISREG(marker.st_mode) or marker.st_uid != os.getuid() or marker.st_mode & 0o077 or marker.st_size:
                    hold_custody()
                marker_seen = marker_seen or time.monotonic()
            except FileNotFoundError:
                pass
        # Let the existing wrapper perform its bounded TERM/KILL path first;
        # then also drain escaped descendants that its process group misses.
        if marker_seen and (code is not None or time.monotonic() - marker_seen >= 1.2):
            interrupted[0] = True
        if interrupted[0]:
            # Cooperative marker cleanup lets the existing stream finish; the
            # host's separate kernel probe remains the only settlement authority.
            drain()
            if not sent:
                code = child.poll()
                os.write(write_fd, str(130 if marker_seen or code is None else code).encode())
                os.close(write_fd)
                sent = True
            hold_custody()
        time.sleep(.02)


def checked_root(nonce, expected):
    pid = expected['pid']
    fd = os.pidfd_open(pid)
    try:
        row = identity(pid)
        if row['startTicks'] != expected['startTicks'] or row['state'] == 'Z':
            raise RuntimeError('exec identity vanished or changed')
        argv = open('/proc/%d/cmdline' % pid, 'rb').read().split(b'\0')
        if nonce.encode() not in argv or b'launch' not in argv:
            raise RuntimeError('exec process binding mismatch')
        return fd
    except BaseException:
        os.close(fd)
        raise


def release(nonce, expected):
    fd = checked_root(nonce, expected)
    try:
        signal.pidfd_send_signal(fd, signal.SIGUSR2)
    finally:
        os.close(fd)
    return {'admitted': True, 'nonce': nonce, **expected}


def finish(nonce, terminate, expected, cancel_marker):
    # Guest-written owner/done records are intentionally never read here.
    if cancel_marker:
        try:
            marker = os.lstat(cancel_marker)
            if not stat.S_ISREG(marker.st_mode) or marker.st_uid != os.getuid() or marker.st_mode & 0o077 or marker.st_size:
                raise RuntimeError('invalid cancellation marker')
            terminate = True
        except FileNotFoundError:
            pass
    fd = checked_root(nonce, expected)
    try:
        signal.pidfd_send_signal(fd, signal.SIGSTOP)
        deadline = time.monotonic() + 5
        while identity(expected['pid'])['state'] not in ('T', 't'):
            if time.monotonic() >= deadline:
                raise RuntimeError('supervisor did not stop')
            time.sleep(.005)
        if terminate:
            while True:
                # The stopped subreaper retains orphaned grandchildren. Stop
                # every live descendant before killing and repeat kernel census.
                check = checked_root(nonce, expected)
                os.close(check)
                rows = [r for r in descendants(expected['pid']) if r['state'] != 'Z']
                if not rows:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError('owned descendants did not drain')
                for row in rows:
                    send_exact(row, signal.SIGSTOP)
                for row in descendants(expected['pid']):
                    if row['state'] != 'Z':
                        send_exact(row, signal.SIGKILL)
                time.sleep(.01)
        check = checked_root(nonce, expected)
        os.close(check)
        signal.pidfd_send_signal(fd, signal.SIGKILL)
        poll = select.poll()
        poll.register(fd, select.POLLIN)
        if not poll.poll(1000):
            raise RuntimeError('supervisor termination unacknowledged')
        return {'nonce': nonce, **expected, 'settled': True,
                'disposition': 'drained' if terminate else 'released',
                'descendantsDrained': terminate, 'authority': 'independent-kernel-probe'}
    finally:
        os.close(fd)


if __name__ == '__main__':
    mode, folder, nonce, root_identity, cancel_marker, value = sys.argv[1:]
    info = os.lstat(folder)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise RuntimeError('exec custody directory is not private')
    DIRECTORY_ID = json.loads(root_identity)
    check_directory(folder)
    DIRECTORY_FD = os.open(folder, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    opened = os.fstat(DIRECTORY_FD)
    if [opened.st_dev, opened.st_ino] != DIRECTORY_ID:
        raise RuntimeError('exec custody directory changed while opening')
    if mode == 'launch':
        sys.exit(launch(folder, nonce, value, cancel_marker))
    if mode in ('finish', 'release'):
        request = json.loads(value)
        result = release(nonce, request['identity']) if mode == 'release' else finish(nonce, request['terminate'], request['identity'], cancel_marker)
        print(json.dumps(result))
    else:
        raise RuntimeError('unsupported custody operation')
