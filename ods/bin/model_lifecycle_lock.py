"""Shared Linux lifecycle flock, with descriptor-relative custody checks."""
import os
from pathlib import Path
import stat
import sys


def key_for(path):
    # POSIX cksum, including its byte-count trailer (the shell uses cksum).
    data = os.fsencode(path) + b'\0'
    length = len(data)
    trailer = []
    while length:
        trailer.append(length & 255)
        length >>= 8
    crc = 0
    for byte in [*data, *trailer]:
        crc ^= byte << 24
        for _ in range(8):
            crc = ((crc << 1) ^ (0x04c11db7 if crc & 0x80000000 else 0)) & 0xffffffff
    return str(crc ^ 0xffffffff)


def acquire(install):
    if sys.platform != 'linux': return None
    import fcntl
    install = Path(install).resolve(strict=True)
    uid = install.stat().st_uid
    root = Path(os.environ.get('ODS_MODEL_LIFECYCLE_LOCK_ROOT', f'/tmp/ods-model-lifecycle-{uid}'))
    created = False
    try:
        root.mkdir(mode=0o700)
        created = True
    except FileExistsError: pass
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(directory)
        if created and os.geteuid() == 0 and info.st_uid == 0 and info.st_mode & 0o077 == 0:
            os.fchown(directory, uid, -1)
            info = os.fstat(directory)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != uid or info.st_mode & 0o077:
            raise RuntimeError('Unsafe model lifecycle lock directory')
        name = f'ods-model-lifecycle-{key_for(install)}.lock'
        created_file = False
        try:
            fd = os.open(name, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            created_file = True
        except FileExistsError:
            fd = os.open(name, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_mode & 0o077:
                raise RuntimeError('Unsafe model lifecycle lock file')
            if created_file and os.geteuid() == 0 and info.st_uid == 0:
                os.fchown(fd, uid, -1)
            if os.fstat(fd).st_uid != uid:
                raise RuntimeError('Unsafe model lifecycle lock owner')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        return fd
    finally:
        os.close(directory)


def release(fd):
    if fd is not None: os.close(fd)


def hold(install, parent, wait):
    import select
    import time
    if not 0 <= wait <= 300: raise ValueError('Lifecycle wait must be 0..300 seconds')
    # PID plus kernel start time: an orphan or a reused PID cannot retain this
    # lock. No service work occurs here; only this private lock fd is held.
    proc = Path(f'/proc/{parent}/stat')
    identity = proc.read_text().rsplit(')', 1)[1].split()[19]
    def alive():
        try: return proc.read_text().rsplit(')', 1)[1].split()[19] == identity
        except OSError: return False
    deadline = time.monotonic() + wait
    while True:
        try:
            fd = acquire(install)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline or not alive(): raise
            time.sleep(0.1)
    try:
        print('locked', flush=True)
        while alive():
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
            if ready: break  # Explicit release or closed parent pipe.
    finally:
        release(fd)


if __name__ == '__main__':
    if len(sys.argv) != 5 or sys.argv[1] != 'hold':
        raise SystemExit('usage: model_lifecycle_lock.py hold INSTALL PARENT_PID WAIT_SECONDS')
    hold(sys.argv[2], int(sys.argv[3]), float(sys.argv[4]))
