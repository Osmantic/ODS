#!/usr/bin/env python3
"""Prepare and repair the shared Linux extension lock without following paths.

Creation runs as the installer. Repair may run as root or inside Docker's user
namespace. The data-directory owner and Dashboard GID share one unchanged flock
inode; permissions are changed only through its verified open descriptor.
"""
import os
import sys
import stat
import json
import errno

_LOCK_NAME = ".extensions-lock"
_DASHBOARD_GID = 1000


def _open_dir(data_directory):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    return os.open(data_directory, flags)


def _fstat_regular_single(fd):
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.EINVAL, "not a regular file")
    if st.st_nlink != 1:
        raise OSError(errno.EMLINK, "multiple hard links")
    return st


def _check_entry_matches(dir_fd, name, expected_ino, expected_dev):
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise OSError(errno.ENOENT, "directory entry missing")
    if st.st_ino != expected_ino or st.st_dev != expected_dev:
        raise OSError(errno.ESTALE, "directory entry changed")


def prepare(data_directory):
    dir_fd = _open_dir(data_directory)
    lock_fd = None
    created = False
    try:
        try:
            previous_umask = os.umask(0o007)
            try:
                lock_fd = os.open(
                    _LOCK_NAME,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o660,
                    dir_fd=dir_fd,
                )
            finally:
                os.umask(previous_umask)
            created = True
        except FileExistsError:
            lock_fd = os.open(
                _LOCK_NAME,
                os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=dir_fd,
            )
        st = _fstat_regular_single(lock_fd)
        _check_entry_matches(dir_fd, _LOCK_NAME, st.st_ino, st.st_dev)
        return {
            "created": created,
            "uid": st.st_uid,
            "gid": st.st_gid,
            "mode": stat.S_IMODE(st.st_mode),
            "inode": st.st_ino,
        }
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(dir_fd)


def repair(data_directory):
    dir_fd = _open_dir(data_directory)
    lock_fd = None
    try:
        lock_fd = os.open(
            _LOCK_NAME,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
            dir_fd=dir_fd,
        )
        st = _fstat_regular_single(lock_fd)
        _check_entry_matches(dir_fd, _LOCK_NAME, st.st_ino, st.st_dev)
        dir_st = os.fstat(dir_fd)
        target_uid = dir_st.st_uid
        target_gid = _DASHBOARD_GID
        target_mode = 0o660
        if st.st_uid != target_uid or st.st_gid != target_gid:
            os.fchown(lock_fd, target_uid, target_gid)
        st_after_chown = _fstat_regular_single(lock_fd)
        if st_after_chown.st_uid != target_uid or st_after_chown.st_gid != target_gid:
            raise OSError(errno.EPERM, "ownership repair failed")
        if stat.S_IMODE(st_after_chown.st_mode) != target_mode:
            os.fchmod(lock_fd, target_mode)
        st2 = _fstat_regular_single(lock_fd)
        if st2.st_uid != target_uid or st2.st_gid != target_gid:
            raise OSError(errno.EPERM, "ownership repair failed")
        if stat.S_IMODE(st2.st_mode) != target_mode:
            raise OSError(errno.EPERM, "mode repair failed")
        _check_entry_matches(dir_fd, _LOCK_NAME, st2.st_ino, st2.st_dev)
        return {
            "uid": st2.st_uid,
            "gid": st2.st_gid,
            "mode": stat.S_IMODE(st2.st_mode),
            "inode": st2.st_ino,
        }
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
        os.close(dir_fd)


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 2 or argv[0] not in ("prepare", "repair"):
        print("usage: dashboard-lock.py prepare|repair <data_directory>", file=sys.stderr)
        return 2
    mode, directory = argv
    try:
        if mode == "prepare":
            result = prepare(directory)
        else:
            result = repair(directory)
        print(json.dumps(result))
        return 0
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
