#!/usr/bin/env python3
"""
renameat2_noreplace.py — fixed-purpose helper for the credential ingress core.

Calls the Linux renameat2(2) syscall with RENAME_NOREPLACE to atomically
rename a file within an already-pinned directory fd.  No secret bytes are
passed.  Accepts only two closed-registry basenames (validated: no /, no ..).
Falls through to an error exit if the target name already exists.

The parent Node process maps the pinned directory descriptor to a fixed child
fd (fd 3) via spawnSync stdio.  The helper reads that fd and fstats it to
confirm it is a real directory before the syscall.

Usage (internal only — called by provider-credential-ingress-core.mjs):
  python3 renameat2_noreplace.py <oldname> <newname>
"""
import ctypes
import ctypes.util
import errno
import os
import stat
import sys

# ---------------------------------------------------------------------------
# Closed credential filename registry — only these final names may be targets
# ---------------------------------------------------------------------------
ALLOWED_FINAL_NAMES = frozenset({
    "moonshot-kimi-key",
    "openai-key",
    "anthropic-key",
})

# Staging pattern: must start with .credential-staging-
STAGING_PREFIX = ".credential-staging-"

# Fixed child descriptor for the parent-pinned directory fd
DIR_FD = 3


def _fail(msg: str) -> None:
    print(f"renameat2: {msg}", file=sys.stderr)
    sys.exit(70)


def main() -> None:
    # -----------------------------------------------------------------------
    # Platform gate: Linux only
    # -----------------------------------------------------------------------
    try:
        uname = os.uname()
    except AttributeError:
        _fail("renameat2 is not available on this platform")
    if uname.sysname != "Linux":
        _fail(f"renameat2 is not available on {uname.sysname}")

    # -----------------------------------------------------------------------
    # Argument validation — exactly two basenames, no dirfd in argv
    # -----------------------------------------------------------------------
    if len(sys.argv) != 3:
        _fail("usage: renameat2_noreplace.py <oldname> <newname>")

    oldname_s, newname_s = sys.argv[1], sys.argv[2]

    # -----------------------------------------------------------------------
    # Basename validation: simple names only (no /, no ., no ..)
    # -----------------------------------------------------------------------
    for name, label in ((oldname_s, "oldname"), (newname_s, "newname")):
        if not name:
            _fail(f"{label} must not be empty")
        if "/" in name or name == "." or name == "..":
            _fail(f"{label} must be a simple basename (no /, ., ..)")
        if len(name) > 255:
            _fail(f"{label} exceeds MAXNAMELEN")

    # -----------------------------------------------------------------------
    # Closed registry: oldname must be a staging file, newname must be
    # a registered credential filename. No arbitrary paths.
    # -----------------------------------------------------------------------
    if not oldname_s.startswith(STAGING_PREFIX):
        _fail(f"oldname must be a staging file ({STAGING_PREFIX}*)")
    if newname_s not in ALLOWED_FINAL_NAMES:
        _fail(f"newname must be in the closed credential registry")

    # -----------------------------------------------------------------------
    # Verify the parent-pinned directory fd is open and is a directory
    # -----------------------------------------------------------------------
    try:
        dir_stat = os.fstat(DIR_FD)
    except OSError as e:
        _fail(f"parent directory fd {DIR_FD} not available: {e}")
    if not stat.S_ISDIR(dir_stat.st_mode):
        _fail(f"parent fd {DIR_FD} is not a directory")

    # -----------------------------------------------------------------------
    # Verify /proc/self/fd/<DIR_FD> is accessible (fail-closed requirement)
    # -----------------------------------------------------------------------
    proc_link = f"/proc/self/fd/{DIR_FD}"
    try:
        proc_stat = os.stat(proc_link)
    except OSError as e:
        _fail(f"/proc/self/fd/{DIR_FD} unavailable: {e}")

    # -----------------------------------------------------------------------
    # Load libc with explicit ctypes signatures
    # -----------------------------------------------------------------------
    libc_path = ctypes.util.find_library("c")
    if not libc_path:
        _fail("cannot find libc")
    libc = ctypes.CDLL(libc_path, use_errno=True)

    # Explicit argtypes and restype for renameat2
    libc.renameat2.argtypes = [
        ctypes.c_int,      # olddirfd
        ctypes.c_char_p,   # oldpath
        ctypes.c_int,      # newdirfd
        ctypes.c_char_p,   # newpath
        ctypes.c_uint,     # flags
    ]
    libc.renameat2.restype = ctypes.c_int

    # RENAME_NOREPLACE: 1 << 0 = 1
    RENAME_NOREPLACE = 1

    rc = libc.renameat2(
        DIR_FD, oldname_s.encode("utf-8"),
        DIR_FD, newname_s.encode("utf-8"),
        RENAME_NOREPLACE,
    )

    if rc != 0:
        err = ctypes.get_errno()
        if err == errno.EEXIST:
            _fail("target already exists (RENAME_NOREPLACE)")
        _fail(f"renameat2 failed: {errno.errorcode.get(err, 'unknown')}")

    sys.exit(0)


if __name__ == "__main__":
    main()
