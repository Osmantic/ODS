#!/usr/bin/env python3
"""Streaming SHA256 for installer model files.

Prints exactly one lowercase hex digest plus newline on success.
Prints nothing on stdout on failure. Advisory cache hints are best-effort
and never affect digest correctness.
"""
import hashlib
import os
import stat
import sys

CHUNK = 4 * 1024 * 1024


def _stat_key(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def hash_file(path):
    """Return lowercase hex SHA256 of regular file at path.

    Raises OSError or ValueError on any failure. Digest is returned only
    after the descriptor has been closed successfully.
    """
    initial = os.stat(path)
    if not stat.S_ISREG(initial.st_mode):
        raise ValueError("not a regular file")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    warned = False

    def advise(offset, length, name):
        nonlocal warned
        fn = getattr(os, "posix_fadvise", None)
        advice = getattr(os, name, None)
        if fn is None or advice is None:
            return
        try:
            fn(fd, offset, length, advice)
        except OSError:
            if not warned:
                warned = True
                # Advisory errors do not invalidate a correctly read digest.
                try:
                    print("model-file-sha256: cache advice unavailable; continuing verification", file=sys.stderr)
                except OSError:
                    pass

    try:
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("not a regular file")
        if _stat_key(opened) != _stat_key(initial):
            raise ValueError("file changed before open")

        advise(0, 0, "POSIX_FADV_SEQUENTIAL")

        h = hashlib.sha256()
        total = 0
        size = initial.st_size
        while total < size:
            want = min(CHUNK, size - total)
            chunk = os.read(fd, want)
            if not chunk:
                raise ValueError("unexpected EOF")
            h.update(chunk)
            advise(total, len(chunk), "POSIX_FADV_DONTNEED")
            total += len(chunk)

        extra = os.read(fd, 1)
        if extra:
            raise ValueError("file grew during read")

        after = os.fstat(fd)
        if _stat_key(after) != _stat_key(initial):
            raise ValueError("file changed during read")

        final = os.stat(path)
        if _stat_key(final) != _stat_key(initial):
            raise ValueError("path changed during read")

        digest = h.hexdigest()
    finally:
        os.close(fd)

    return digest


def main(argv):
    if len(argv) != 2:
        return 2
    try:
        digest = hash_file(argv[1])
    except (OSError, ValueError) as exc:
        try:
            sys.stderr.write("model-file-sha256: error: %s\n" % exc)
            sys.stderr.flush()
        except Exception:
            pass
        return 1
    try:
        sys.stdout.write(digest + "\n")
        sys.stdout.flush()
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
