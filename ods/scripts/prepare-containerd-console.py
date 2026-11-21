#!/usr/bin/env python3
"""Narrow ODS repair: add DeviceAllow=/dev/console m to containerd.

Scope: Linux, rootful Docker, NVIDIA-only DeviceAllow policy on containerd.
Only repairs the exact known auto+nonempty+NVIDIA-only+rwm case by adding
an owned drop-in with `DeviceAllow=/dev/console m` (create-only, never rwm).
Never restarts containerd, never edits vendor drop-ins, never touches
DevicePolicy or existing DeviceAllow entries.

Usage:
  prepare-containerd-console.py [--check | --apply]

Exit codes:
  0  no repair needed (or apply succeeded)
  10 repair needed (--check only)
  1  error / refused / apply failed
"""

import argparse
import os
import re
import stat
import subprocess
import sys

UNIT = "containerd.service"
DROPIN_DIR = "/etc/systemd/system/containerd.service.d"
DROPIN_PATH = os.path.join(DROPIN_DIR, "90-ods-image-unpack.conf")
DROPIN_CONTENT = (
    "# Managed by ODS: allow image unpack to create /dev/console (mknodat).\n"
    "# Create-only; never grants read/write on /dev/console.\n"
    "[Service]\n"
    "DeviceAllow=/dev/console m\n"
)

# Known NVIDIA-only DeviceAllow patterns (device path, perms).
_NVIDIA_DEV_RE = re.compile(r"^/dev/nvidia(?:[A-Za-z0-9_/-]*)$")
_NVIDIA_CAPS_RE = re.compile(r"^char-nvidia-caps$")


def _run_systemctl_show():
    """Return dict of systemctl show properties for containerd.service."""
    try:
        proc = subprocess.run(
            ["systemctl", "show", UNIT,
             "--property=ActiveState",
             "--property=DevicePolicy",
             "--property=DeviceAllow"],
            capture_output=True, text=True, check=True, timeout=15,
        )
    except FileNotFoundError:
        return None
    props: dict[str, list[str]] = {}
    for line in proc.stdout.splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        props.setdefault(key, []).append(val)
    return props


def _parse_device_allow(entries):
    """Parse repeated DeviceAllow= values into [(device, perms), ...].

    Each entry is 'device perms' with perms at the last space.
    """
    out = []
    for raw in entries:
        raw = raw.strip()
        if not raw:
            continue
        idx = raw.rfind(" ")
        if idx <= 0:
            out.append((raw, ""))
        else:
            out.append((raw[:idx], raw[idx + 1:]))
    return out


def _is_nvidia_only(entries):
    """True iff every entry is a known NVIDIA pattern with rwm perms."""
    if not entries:
        return False
    for dev, perms in entries:
        if perms != "rwm":
            return False
        if _NVIDIA_DEV_RE.match(dev):
            continue
        if _NVIDIA_CAPS_RE.match(dev):
            continue
        return False
    return True


def _has_console_m(entries):
    for dev, perms in entries:
        if dev == "/dev/console" and "m" in perms:
            return True
    return False


def classify(props):
    """Return (needs_repair: bool, reason: str)."""
    if props is None:
        return False, "systemctl unavailable or unit not queryable"
    active = (props.get("ActiveState") or [""])[0]
    if active != "active":
        return False, "containerd.service not active (%s)" % (active or "unknown")
    policy = (props.get("DevicePolicy") or [""])[0]
    if policy != "auto":
        return False, "DevicePolicy=%s (not auto); manual review if image unpack fails" % (policy or "unknown")
    entries = _parse_device_allow(props.get("DeviceAllow") or [])
    if not entries:
        return False, "DeviceAllow empty (unrestricted); no repair"
    if _has_console_m(entries):
        return False, "already has DeviceAllow=/dev/console m"
    if not _is_nvidia_only(entries):
        return False, "custom/closed DeviceAllow policy; manual review if image unpack fails"
    return True, "NVIDIA-only DeviceAllow with rwm; /dev/console m missing"


def _is_root():
    return os.geteuid() == 0


def _read_existing():
    """Return (state, content) where state in {'absent','owned','foreign','symlink'}."""
    try:
        st = os.lstat(DROPIN_PATH)
    except FileNotFoundError:
        return "absent", None
    if stat.S_ISLNK(st.st_mode):
        return "symlink", None
    if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1
            or st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o022):
        return "foreign", None
    with open(DROPIN_PATH, "r", encoding="utf-8") as f:
        content = f.read()
    if content == DROPIN_CONTENT:
        return "owned", content
    return "foreign", content


def _ensure_dir():
    if os.path.islink(DROPIN_DIR):
        raise RuntimeError("refusing: %s is a symlink" % DROPIN_DIR)
    if not os.path.isdir(DROPIN_DIR):
        os.makedirs(DROPIN_DIR, mode=0o755, exist_ok=True)
        os.chmod(DROPIN_DIR, 0o755)
    if os.path.islink(DROPIN_DIR):
        raise RuntimeError("refusing: %s became a symlink" % DROPIN_DIR)
    st = os.stat(DROPIN_DIR)
    if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) & 0o022:
        raise RuntimeError("refusing: containerd drop-in directory is not owner-controlled")


def _write_owned():
    """Create the owned drop-in atomically with O_EXCL/O_NOFOLLOW."""
    _ensure_dir()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(DROPIN_PATH, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(fd, 0o644)
        stream.write(DROPIN_CONTENT)
        stream.flush()
        os.fsync(fd)


def _daemon_reload():
    proc = subprocess.run(
        ["systemctl", "daemon-reload"],
        capture_output=True, text=True, check=False, timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError("systemctl daemon-reload failed: %s" % (proc.stderr.strip() or proc.returncode))


def _verify_after_apply(old_entries):
    props = _run_systemctl_show()
    if props is None:
        raise RuntimeError("cannot verify: systemctl show failed after apply")
    active = (props.get("ActiveState") or [""])[0]
    if active != "active":
        raise RuntimeError("containerd.service not active after apply (%s)" % (active or "unknown"))
    if props.get("DevicePolicy") != ["auto"]:
        raise RuntimeError("containerd DevicePolicy changed during repair; review host policy")
    new_entries = _parse_device_allow(props.get("DeviceAllow") or [])
    if not _has_console_m(new_entries):
        raise RuntimeError("effective DeviceAllow missing /dev/console m after apply")
    new_set = set(new_entries)
    for e in old_entries:
        if e not in new_set:
            raise RuntimeError("effective DeviceAllow lost prior entry %r" % (e,))


def do_check():
    props = _run_systemctl_show()
    needs, reason = classify(props)
    print("containerd console policy: %s" % reason)
    return 10 if needs else 0


def do_apply():
    props = _run_systemctl_show()
    needs, reason = classify(props)
    if not needs:
        print("containerd console policy: %s" % reason)
        return 0
    if not _is_root():
        print("error: --apply requires root", file=sys.stderr)
        return 1

    old_entries = _parse_device_allow(props.get("DeviceAllow") or [])
    try:
        _ensure_dir()
    except (OSError, RuntimeError) as exc:
        print("error: unsafe drop-in directory: %s" % exc, file=sys.stderr)
        return 1
    state, _ = _read_existing()
    if state == "symlink":
        print("error: refusing: %s is a symlink" % DROPIN_PATH, file=sys.stderr)
        return 1
    if state == "foreign":
        print("error: refusing: %s exists with foreign content" % DROPIN_PATH, file=sys.stderr)
        return 1

    if state == "absent":
        try:
            _write_owned()
        except FileExistsError:
            # Race: someone created it; re-check ownership.
            state2, _ = _read_existing()
            if state2 != "owned":
                print("error: refusing: %s appeared with foreign content" % DROPIN_PATH, file=sys.stderr)
                return 1
        except (OSError, RuntimeError) as e:
            print("error: write failed: %s" % e, file=sys.stderr)
            return 1
    # state == 'owned': retry daemon-reload (safe idempotent path).

    try:
        _daemon_reload()
        _verify_after_apply(old_entries)
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 1

    print("containerd console policy: repaired (DeviceAllow=/dev/console m)")
    return 0


def main(argv=None):
    if not sys.platform.startswith("linux"):
        print("containerd console policy: only applicable to Linux")
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="report whether repair is needed (default)")
    g.add_argument("--apply", action="store_true", help="apply the repair")
    args = parser.parse_args(argv)
    if args.apply:
        return do_apply()
    return do_check()


if __name__ == "__main__":
    sys.exit(main())
