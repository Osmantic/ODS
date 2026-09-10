"""Persistent controller for Pixel full-access mode on the ODS host agent box.

Owns the real state transition around the pure helper in access_mode_config.py:
security-checked config file handling, staged validation with the OpenClaw CLI
(or an injected validator), atomic config replacement, restart/health hook,
rollback on failure, and a managed receipt in a private state directory.

Callable contract (all functions return plain dicts; failures raise
AccessModeError subclasses with a stable ``code``; messages never contain
configuration values or secrets):

    enable_full_access(config_path, state_dir=None, *, confirmed=False,
                       validate_config=None, restart=None,
                       check_no_active_run=None, lock_timeout=10.0, now=None)
        -> {"status": "full-access", "baseline": {...}, "config_sha256": h,
            "recovered_stale_apply": bool}

        Requires confirmed=True, a live no-active-run check (rejects when the
        hook is missing or reports a run), staged config validation, atomic
        replace, and a successful restart()+health hook. Repeated enable never
        replaces the original baseline. A stale "pending" receipt from a
        crashed apply is rolled back to the original baseline first.

    restore_sandbox(config_path, state_dir=None, *, validate_config=None,
                    restart=None, check_no_active_run=None, lock_timeout=10.0)
        -> {"status": "sandboxed", "already_restored": bool}

        Restores only the five baseline fields (unrelated edits preserved),
        validates the staged result, restarts, then removes the managed
        receipt only after full success. Idempotent completion: if the fields
        already match the baseline (crash after replace), restart and verify
        health before clearing state. Both transitions require live idle checks.

    get_status(config_path, state_dir=None)
        -> {"status": "sandboxed"|"full-access"|"unknown", "managed": bool,
            "receipt_status": str|None, "config_sha256": str|None,
            "reasons": [str, ...]}

        Derived from the actual effective per-agent overrides plus the managed
        receipt. Never returns field values, credentials, or file contents.

Injected hooks:
    validate_config(path:str) -> bool | (bool, reason)   default: OpenClaw CLI
        load-validation with OPENCLAW_CONFIG_PATH pointed at the staged file.
    restart() -> truthy on healthy after restart; raise/False = failure.
        Must restart the service AND verify health.
    check_no_active_run() -> False when idle; True when busy.
        Missing, failed, or non-boolean checks fail closed.

The caller owns authentication, endpoint exposure, and restart/health checks.
This POSIX controller modifies the selected config and private state directory;
it is not yet connected to ODS Settings or exposed as a Pixel tool.
"""

import errno
import fcntl
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_helper():
    spec = importlib.util.spec_from_file_location(
        "_ods_access_mode_config", os.path.join(_HERE, "access_mode_config.py"))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_helper = _load_helper()
MigrationError = _helper.MigrationError
ENABLED_VALUES = _helper.ENABLED_VALUES
PATHS = _helper.PATHS
PATH_KEYS = _helper.PATH_KEYS

CONTROLLER_VERSION = 1
STATUS_ENABLED = "full-access"
STATUS_PENDING = "pending"
RECEIPT_NAME = "pixel-access-mode.json"
LOCK_NAME = "apply.lock"
DEFAULT_LOCK_TIMEOUT = 10.0


class AccessModeError(Exception):
    """Base controller error; ``code`` is a stable machine-readable token."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class AccessModeRejected(AccessModeError):
    """Operator/validation refusal; nothing was changed."""


class AccessModeSecurity(AccessModeError):
    """Insecure path, symlink, ownership, or mode; nothing was changed."""


class AccessModeRace(AccessModeError):
    """Concurrent modification or lock contention; nothing was changed."""


class AccessModeRollback(AccessModeError):
    """A failure occurred and rollback could not fully complete."""


# ---------------------------------------------------------------- utilities

def _default_state_dir():
    base = os.environ.get("XDG_STATE_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "state")
    return os.path.join(base, "ods-pixel-access-mode")


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _read_file(path):
    with open(path, "rb") as fh:
        return fh.read()


def _atomic_write(path, data, mode):
    """Write bytes to path atomically with exact mode (best-effort dir fsync)."""
    parent = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".ods-access-", dir=parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


def _atomic_rollback_write(path, data, mode):
    """Write rollback bytes into place with an original-inode sentinel.

    Creates a private exclusive temp file in the same directory (never a
    predictable pid-based name), writes and fsyncs via the fd, capturing
    the temp file's identity (st_dev/st_ino) with ``os.fstat`` on that
    owned fd while writing, before the fd is closed. Before the rename,
    ``lstat(tmp)`` must still be a regular file with nlink 1 matching the
    captured identity, and ``lstat(path)`` must be a regular file (not
    merely not-a-symlink). The rename itself never follows a symlink, so
    nothing is ever written through a swapped-in link. After the rename,
    ``lstat(path)`` must still match the captured identity without
    following links.

    This is tamper detection against the temp file this helper wrote, not
    a same-UID TOCTOU race fix and not an atomic compare-and-swap; callers
    keep their own hash and restart semantics.
    """
    parent = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".ods-access-rollback-", dir=parent)
    orig = None
    replaced = False
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as fh:
            fd = -1  # fdopen owns it now; never double-close.
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            # Capture identity from the fd we still own, WHILE writing:
            # fstat never follows a path component, so this is exactly the
            # inode this helper wrote, immune to later entry swaps.
            orig = os.fstat(fh.fileno())
            if not stat.S_ISREG(orig.st_mode):
                raise AccessModeRollback(
                    "rollback-blocked-sentinel",
                    "rollback temp file is not a regular file; original "
                    "content NOT written; manual recovery required")
        # Source sanity at rename time: lstat the temp ENTRY, require a
        # regular file, nlink 1, and the identity captured on the owned
        # fd. A replacement regular file or a symlink fails here BEFORE
        # the rename; lstat never follows a final symlink.
        try:
            tmp_stat = os.lstat(tmp)
        except FileNotFoundError:
            raise AccessModeRollback(
                "rollback-blocked-sentinel",
                "rollback temp file was tampered with; original content "
                "NOT written; manual recovery required")
        if (not stat.S_ISREG(tmp_stat.st_mode)
                or tmp_stat.st_nlink != 1
                or tmp_stat.st_dev != orig.st_dev
                or tmp_stat.st_ino != orig.st_ino):
            raise AccessModeRollback(
                "rollback-blocked-sentinel",
                "rollback temp file was replaced with different file "
                "contents or type; original content NOT written; manual "
                "recovery required")
        # Destination sanity at rename time: must be a regular file, not
        # merely not-a-symlink. No identity requirement: the rename
        # replaces this inode by design.
        try:
            dst_stat = os.lstat(path)
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ENOTDIR):
                raise AccessModeRollback(
                    "rollback-blocked-vanished",
                    "config path disappeared during rollback; original "
                    "content NOT written; manual recovery required")
            raise
        if stat.S_ISLNK(dst_stat.st_mode):
            raise AccessModeRollback(
                "rollback-blocked-symlink",
                "config path is a symlink; original content NOT "
                "written; manual recovery required")
        if not stat.S_ISREG(dst_stat.st_mode):
            raise AccessModeRollback(
                "rollback-blocked-type",
                "config path is not a regular file; original content "
                "NOT written; manual recovery required")
        os.replace(tmp, path)
        replaced = True
        # Post-rename: the path must now refer to exactly the inode written
        # here, compared without following links.
        now = os.lstat(path)
        if (not stat.S_ISREG(now.st_mode)
                or now.st_dev != orig.st_dev
                or now.st_ino != orig.st_ino):
            raise AccessModeRollback(
                "rollback-sentinel-raced",
                "rollback rename completed but the path changed again "
                "immediately afterwards; verify rolled-back content "
                "manually")
        try:
            dfd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        if fd != -1:
            try:
                os.close(fd)
            except OSError:
                pass
        if not replaced:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _lcheck(path, *, kind, owner, max_wide_bits, label):
    """lstat checks: type, not a symlink, owner, and no group/other bits."""
    try:
        st = os.lstat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            raise AccessModeRejected("config-missing",
                                     "%s does not exist: %s" % (label, path))
        raise AccessModeSecurity("stat-failed",
                                 "cannot stat %s: %s" % (kind, exc.errno))
    if stat.S_ISLNK(st.st_mode):
        raise AccessModeSecurity("symlink-%s" % kind,
                                 "%s must not be a symlink" % label)
    want = stat.S_ISREG if kind == "file" else stat.S_ISDIR
    if not want(st.st_mode):
        raise AccessModeSecurity("bad-type-%s" % kind,
                                 "%s must be a regular %s" % (label, kind))
    if owner and st.st_uid != os.geteuid():
        raise AccessModeSecurity("owner-%s" % kind,
                                 "%s must be owned by the current user" % label)
    if st.st_mode & max_wide_bits:
        raise AccessModeSecurity(
            "insecure-mode-%s" % kind,
            "%s has insecure group/other permission bits" % label)
    return st


def _check_config_security(config_path):
    """Config file, its parent, and the full path chain must be safe."""
    p = os.path.abspath(config_path)
    st = _lcheck(p, kind="file", owner=True, max_wide_bits=0o022,
                 label="config file")
    parent = os.path.dirname(p)
    _lcheck(parent, kind="dir", owner=True, max_wide_bits=0o022,
            label="config directory")
    if os.path.realpath(p) != p:
        raise AccessModeSecurity("symlink-path",
                                 "config path resolves through a symlink")
    return p, st.st_mode & 0o777


def _prepare_state_dir(state_dir):
    sd = os.path.abspath(state_dir)
    if os.path.lexists(sd):
        _lcheck(sd, kind="dir", owner=True, max_wide_bits=0o077,
                label="state directory")
        if os.path.realpath(sd) != sd:
            raise AccessModeSecurity("symlink-path",
                                     "state directory resolves through a symlink")
    else:
        # Create the missing chain under the nearest existing ancestor.
        ancestor = os.path.dirname(sd)
        created = []
        while not os.path.lexists(ancestor):
            created.append(ancestor)
            ancestor = os.path.dirname(ancestor)
        _lcheck(ancestor, kind="dir", owner=True, max_wide_bits=0o022,
                label="state parent directory")
        if os.path.realpath(ancestor) != ancestor:
            raise AccessModeSecurity("symlink-path",
                                     "state directory resolves through a symlink")
        os.makedirs(sd, mode=0o700)
        for path in created:
            os.chmod(path, 0o700)  # defeat umask on intermediates
        os.chmod(sd, 0o700)
        _lcheck(sd, kind="dir", owner=True, max_wide_bits=0o077,
                label="state directory")
    return sd


class _Lock(object):
    """Exclusive flock on state_dir/apply.lock; shared mode for status."""

    def __init__(self, state_dir, exclusive=True, timeout=DEFAULT_LOCK_TIMEOUT):
        self.path = os.path.join(state_dir, LOCK_NAME)
        self.exclusive = exclusive
        self.timeout = timeout
        self.fd = None

    def __enter__(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        st = os.fstat(fd)
        if st.st_uid != os.geteuid() or (st.st_mode & 0o077):
            os.close(fd)
            raise AccessModeSecurity("insecure-mode-lockfile",
                                     "lock file has insecure ownership/mode")
        deadline = self.timeout
        import time as _t
        waited = 0.0
        while True:
            flags = (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
            try:
                fcntl.flock(fd, flags)
                break
            except OSError:
                if waited >= deadline:
                    os.close(fd)
                    raise AccessModeRace(
                        "lock-busy", "another apply/restore holds the lock")
                _t.sleep(0.05)
                waited += 0.05
        self.fd = fd
        return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                fcntl.flock(self.fd, fcntl.LOCK_UN)
            finally:
                os.close(self.fd)
                self.fd = None
        return False


def _normalize_validate(fn):
    """Accept bool or (bool, reason) returns; anything else is invalid."""
    if fn is None:
        return None

    def wrapped(path):
        out = fn(path)
        if isinstance(out, tuple) and len(out) == 2:
            ok, reason = out
        else:
            ok, reason = out, ""
        if not isinstance(ok, bool):
            raise AccessModeRejected(
                "validator-contract", "validate_config must return bool")
        return ok, reason or ""
    return wrapped


def default_validate_config(path):
    """Validate a staged config file via the OpenClaw CLI (load-validation).

    Every CLI command parses the config under OPENCLAW_CONFIG_PATH and exits
    non-zero with schema errors on an invalid file; `config get agents` is the
    cheapest such command. Returns (ok, reason); reasons contain schema paths
    only, never values.
    """
    exe = shutil.which("openclaw")
    if not exe:
        return False, "openclaw CLI not found"
    env = dict(os.environ)
    env["OPENCLAW_CONFIG_PATH"] = path
    try:
        proc = subprocess.run(
            [exe, "config", "get", "agents"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False, "openclaw validation timed out"
    except OSError as exc:
        return False, "openclaw validation failed to run (%s)" % exc.errno
    if proc.returncode == 0:
        return True, ""
    # CLI diagnostics can contain rejected configuration values, including
    # provider credentials. Expose a stable error rather than raw output.
    return False, "OpenClaw rejected the staged configuration"


# ------------------------------------------------------- config primitives

def _load_config(config_path):
    """Security checks, read, hash, parse. Returns (abs_path, mode, data, cfg)."""
    p, mode = _check_config_security(config_path)
    try:
        data = _read_file(p)
    except OSError:
        raise AccessModeRejected("config-missing",
                                 "config file does not exist: %s" % p)
    try:
        cfg = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AccessModeRejected("config-malformed",
                                 "config file is not valid JSON")
    return p, mode, data, cfg


def _stage_and_validate(abs_path, new_bytes, cfg_mode, validate, what):
    """Stage new bytes next to the config, validate the staged file."""
    parent = os.path.dirname(abs_path)
    fd, tmp = tempfile.mkstemp(prefix=".ods-access-stage-", dir=parent)
    staged = None
    try:
        os.fchmod(fd, cfg_mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(new_bytes)
            fh.flush()
            os.fsync(fh.fileno())
        staged = tmp
        if validate is not None:
            ok, reason = validate(staged)
            if not ok:
                raise AccessModeRejected(
                    "validation-failed",
                    "staged config failed validation: %s" % reason)
    except BaseException:
        if staged:
            try:
                os.unlink(staged)
            except OSError:
                pass
        raise
    return staged


def _checked_replace(abs_path, new_bytes, expected_hash, staged):
    """Atomic replace only if the on-disk file still hashes to expected."""
    if _sha256_bytes(_read_file(abs_path)) != expected_hash:
        try:
            os.unlink(staged)
        except OSError:
            pass
        raise AccessModeRace("config-changed-during-apply",
                             "config file changed during apply; nothing written")
    os.replace(staged, abs_path)


def _select_pixel(cfg):
    try:
        _helper._validate_root(cfg)
        return _helper._select_agent(cfg)
    except MigrationError as exc:
        raise AccessModeRejected("config-malformed", str(exc))


def _field_record(agent, path):
    node = agent
    for key in path[:-1]:
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
    if isinstance(node, dict) and path[-1] in node:
        return True, node[path[-1]]
    return False, None


def _managed_field_mismatches(cfg):
    """Dotted paths of the five managed fields that are not at enabled values.

    Boolean field compared by identity (False vs 0/""), strings by value.
    """
    agent = _select_pixel(cfg)
    out = []
    for path, value in ENABLED_VALUES.items():
        present, cur = _field_record(agent, path)
        if not present:
            out.append(".".join(path))
            continue
        if value is False:
            if cur is not False:
                out.append(".".join(path))
        elif cur != value or not isinstance(cur, str):
            out.append(".".join(path))
    return out


def _baseline_matches(cfg, baseline):
    """True when the five fields equal the baseline records exactly."""
    agent = _select_pixel(cfg)
    try:
        _helper._validate_baseline(baseline)
    except MigrationError:
        return False
    for key in PATH_KEYS:
        path = key.split(".")
        present, cur = _field_record(agent, path)
        rec = baseline[key]
        if present != rec["present"]:
            return False
        if present and cur != rec["value"]:
            return False
    return True


# ---------------------------------------------------------------- receipts

def _receipt_path(state_dir):
    return os.path.join(state_dir, RECEIPT_NAME)


def _load_receipt(state_dir):
    """Returns receipt dict or None; raises AccessModeRejected if corrupt."""
    rp = _receipt_path(state_dir)
    if not os.path.exists(rp):
        return None
    try:
        raw = _read_file(rp)
        receipt = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise AccessModeRejected("state-corrupt",
                                 "managed state file is corrupt")
    if not isinstance(receipt, dict) \
            or receipt.get("version") != CONTROLLER_VERSION \
            or receipt.get("status") not in (STATUS_ENABLED, STATUS_PENDING) \
            or not isinstance(receipt.get("baseline"), dict) \
            or not isinstance(receipt.get("config_sha256"), str) \
            or not isinstance(receipt.get("config_path"), str):
        raise AccessModeRejected("state-corrupt",
                                 "managed state file has unexpected shape")
    try:
        _helper._validate_baseline(receipt["baseline"])
    except MigrationError:
        raise AccessModeRejected("state-corrupt",
                                 "managed state baseline is invalid")
    return receipt


def _write_receipt(state_dir, receipt):
    data = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    _atomic_write(_receipt_path(state_dir), data, 0o600)


def _remove_receipt(state_dir):
    try:
        os.unlink(_receipt_path(state_dir))
    except FileNotFoundError:
        pass


def _require_idle(check_no_active_run, staged=None):
    try:
        try:
            busy = check_no_active_run()
        except Exception:
            raise AccessModeRejected("active-run-check-failed", "live run check failed") from None
        if not isinstance(busy, bool):
            raise AccessModeRejected("active-run-check-failed", "live run check must return a boolean")
        if busy:
            raise AccessModeRejected("active-run", "an active run is in progress; try again later")
    except AccessModeRejected:
        if staged is not None:
            try:
                os.unlink(staged)
            except OSError:
                pass
        raise


# ------------------------------------------------------------------- enable

def enable_full_access(config_path, state_dir=None, *, confirmed=False,
                       validate_config=None, restart=None,
                       check_no_active_run=None, lock_timeout=DEFAULT_LOCK_TIMEOUT,
                       now=None, expected_config_sha256=None):
    """Switch the pixel agent to unsandboxed full access (see module docstring)."""
    if not confirmed:
        raise AccessModeRejected(
            "not-confirmed",
            "switching to full access requires confirmed=true")
    if not callable(check_no_active_run):
        raise AccessModeRejected(
            "no-active-run-check",
            "a live no-active-run check function is required")
    if not callable(restart):
        raise AccessModeRejected(
            "no-restart-hook", "a restart function is required")
    validate = _normalize_validate(validate_config)
    sd = _prepare_state_dir(state_dir or _default_state_dir())
    with _Lock(sd, exclusive=True, timeout=lock_timeout):
        if expected_config_sha256 is not None and _sha256_bytes(_load_config(config_path)[2]) != expected_config_sha256:
            raise AccessModeRace("config-changed", "configuration changed since inspection")
        return _enable_locked(config_path, sd, validate, restart,
                              check_no_active_run, confirmed)


def _enable_locked(config_path, sd, validate, restart, check_no_active_run,
                   confirmed):
    abs_path, cfg_mode, data, cfg = _load_config(config_path)
    h0 = _sha256_bytes(data)
    receipt = _load_receipt(sd)  # may raise state-corrupt

    if receipt is not None and receipt.get("config_path") != abs_path:
        raise AccessModeRejected("state-path-mismatch",
                                 "managed state belongs to a different config file")

    _require_idle(check_no_active_run)
    recovered = False
    baseline = None
    if receipt is not None and receipt.get("status") == STATUS_ENABLED:
        # Repeated enable: keep the ORIGINAL baseline, never re-capture.
        if _managed_field_mismatches(cfg):
            raise AccessModeRejected(
                "managed-fields-changed",
                "managed fields changed since enable; restore first")
        baseline = receipt["baseline"]
    elif receipt is not None and receipt.get("status") == STATUS_PENDING:
        # Crashed apply: config may be enabled but unmanaged. Roll the five
        # fields back to the original baseline, then apply fresh from it.
        baseline = receipt["baseline"]
        restored_cfg, _ok = _helper.restore(cfg, baseline)  # validated receipt
        new_bytes = json.dumps(restored_cfg, indent=2) .encode() + b"\n"
        staged = _stage_and_validate(abs_path, new_bytes, cfg_mode, validate,
                                     "recovery")
        _require_idle(check_no_active_run, staged)
        _checked_replace(abs_path, new_bytes, h0, staged)
        cfg, data, h0 = restored_cfg, new_bytes, _sha256_bytes(new_bytes)
        recovered = True

    _require_idle(check_no_active_run)

    if baseline is None:
        try:
            new_cfg, baseline = _helper.enable(cfg)
        except MigrationError as exc:
            raise AccessModeRejected("config-malformed", str(exc))
    else:
        try:
            new_cfg, baseline = _helper.enable(cfg, baseline=baseline)
        except MigrationError as exc:
            raise AccessModeRejected("config-malformed", str(exc))

    new_bytes = json.dumps(new_cfg, indent=2).encode("utf-8") + b"\n"
    staged = _stage_and_validate(abs_path, new_bytes, cfg_mode, validate,
                                 "apply")

    _require_idle(check_no_active_run, staged)
    # Crash-safety: pending receipt + original-bytes backup BEFORE replace.
    _atomic_write(_receipt_path(sd), json.dumps({
        "version": CONTROLLER_VERSION, "status": STATUS_PENDING,
        "config_path": abs_path, "config_sha256": h0,
        "baseline": baseline,
    }, indent=2, sort_keys=True).encode() + b"\n", 0o600)
    backup_path = os.path.join(sd, "apply-backup.json")
    _atomic_write(backup_path, data, 0o600)

    try:
        _require_idle(check_no_active_run, staged)
        _checked_replace(abs_path, new_bytes, h0, staged)
    except AccessModeRace:
        if receipt is None:
            _remove_receipt(sd)
        raise

    restart_ok = False
    try:
        restart_ok = bool(restart())
    except Exception:
        restart_ok = False
    if not restart_ok:
        _require_idle(check_no_active_run)
        # Rollback: original config bytes back, then previous running state.
        try:
            if _sha256_bytes(_read_file(abs_path)) == _sha256_bytes(new_bytes):
                _atomic_rollback_write(abs_path, data, cfg_mode)
            else:
                raise AccessModeRollback(
                    "rollback-blocked-hash-mismatch",
                    "config changed during failed apply; original restored "
                    "content NOT written; manual recovery required "
                    "(backup: %s)" % backup_path)
            _remove_receipt(sd)
            back_ok = False
            try:
                back_ok = bool(restart())
            except Exception:
                back_ok = False
            if not back_ok:
                raise AccessModeRollback(
                    "rollback-restart-failed",
                    "config rolled back but restart failed; service health "
                    "must be verified manually")
            raise AccessModeRejected(
                "restart-failed-rolled-back",
                "restart/health verification failed; original config "
                "restored and service restarted")
        except AccessModeError:
            raise
        except OSError as exc:
            raise AccessModeRollback(
                "rollback-write-failed",
                "could not write rollback config: errno %d" % exc.errno)

    _require_idle(check_no_active_run)
    if _sha256_bytes(_read_file(abs_path)) != _sha256_bytes(new_bytes):
        raise AccessModeRace(
            "config-changed-during-apply",
            "config changed during restart; managed pending state retained")
    _atomic_write(_receipt_path(sd), json.dumps({
        "version": CONTROLLER_VERSION, "status": STATUS_ENABLED,
        "config_path": abs_path, "config_sha256": _sha256_bytes(new_bytes),
        "baseline": baseline,
        "updated_at": _utcnow(),
    }, indent=2, sort_keys=True).encode() + b"\n", 0o600)
    try:
        os.unlink(backup_path)
    except FileNotFoundError:
        pass
    return {
        "status": STATUS_ENABLED,
        "baseline": baseline,
        "config_sha256": _sha256_bytes(new_bytes),
        "recovered_stale_apply": recovered,
    }


def _utcnow():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ restore

def restore_sandbox(config_path, state_dir=None, *, validate_config=None,
                    restart=None, check_no_active_run=None,
                    lock_timeout=DEFAULT_LOCK_TIMEOUT, expected_config_sha256=None):
    """Restore the five baseline fields only while idle, with restart and health."""
    if not callable(check_no_active_run):
        raise AccessModeRejected("no-active-run-check", "a live no-active-run check function is required")
    if not callable(restart):
        raise AccessModeRejected("no-restart-hook", "a restart function is required")
    validate = _normalize_validate(validate_config)
    sd = _prepare_state_dir(state_dir or _default_state_dir())
    with _Lock(sd, exclusive=True, timeout=lock_timeout):
        if expected_config_sha256 is not None and _sha256_bytes(_load_config(config_path)[2]) != expected_config_sha256:
            raise AccessModeRace("config-changed", "configuration changed since inspection")
        return _restore_locked(config_path, sd, validate, restart, check_no_active_run)


def _restore_locked(config_path, sd, validate, restart, check_no_active_run):
    abs_path, cfg_mode, data, cfg = _load_config(config_path)
    h0 = _sha256_bytes(data)
    receipt = _load_receipt(sd)
    if receipt is None:
        raise AccessModeRejected("not-managed",
                                 "no managed full-access state exists")
    if receipt.get("config_path") != abs_path:
        raise AccessModeRejected("state-path-mismatch",
                                 "managed state belongs to a different config file")
    if receipt.get("status") not in (STATUS_ENABLED, STATUS_PENDING):
        raise AccessModeRejected("not-managed",
                                 "managed state is not an applied full-access state")
    # An interrupted enable can leave either the original or the atomically
    # enabled fields on disk. Both must be recoverable through Restore. The
    # same baseline/drift checks, live idle checks, validation and restart
    # below still apply; a pending receipt never counts as runtime proof.
    baseline = receipt["baseline"]
    _require_idle(check_no_active_run)

    mismatches = _managed_field_mismatches(cfg)
    if mismatches and not _baseline_matches(cfg, baseline):
        raise AccessModeRejected(
            "managed-fields-changed",
            "managed fields changed since enable; refusing restore")

    already = bool(mismatches)  # fields already at baseline values
    if already:
        # Disk state alone cannot establish that the restarted runtime is safe.
        _require_idle(check_no_active_run)
        try:
            healthy = bool(restart())
        except Exception:
            healthy = False
        if not healthy:
            raise AccessModeRejected("restart-failed", "restart/health verification failed; managed receipt retained")
        _require_idle(check_no_active_run)
        if _sha256_bytes(_read_file(abs_path)) != h0:
            raise AccessModeRace("config-changed-during-apply", "config changed during restart; managed receipt retained")
        _remove_receipt(sd)
        return {"status": "sandboxed", "already_restored": True}

    try:
        new_cfg, _ok = _helper.restore(cfg, baseline)
    except MigrationError as exc:
        raise AccessModeRejected("config-malformed", str(exc))
    new_bytes = json.dumps(new_cfg, indent=2).encode("utf-8") + b"\n"
    staged = _stage_and_validate(abs_path, new_bytes, cfg_mode, validate,
                                 "restore")

    enabled_bytes = data
    try:
        _require_idle(check_no_active_run, staged)
        _checked_replace(abs_path, new_bytes, h0, staged)
    except AccessModeRace:
        raise

    if restart is not None:
        ok = False
        try:
            ok = bool(restart())
        except Exception:
            ok = False
        if not ok:
            _require_idle(check_no_active_run)
            if receipt.get("status") == STATUS_PENDING:
                # Recovery should never broaden access again just because the
                # safer runtime failed to start. Keep the restored fields and
                # original pending receipt so another Restore can verify it.
                raise AccessModeRejected(
                    "restart-failed", "safer configuration written but runtime verification failed; pending receipt retained")
            # Roll back to the enabled config and previous running state.
            if _sha256_bytes(_read_file(abs_path)) == _sha256_bytes(new_bytes):
                _atomic_rollback_write(abs_path, enabled_bytes, cfg_mode)
            else:
                raise AccessModeRollback(
                    "rollback-blocked-hash-mismatch",
                    "config changed during failed restore; enabled content "
                    "NOT rewritten; manual recovery required")
            back_ok = False
            try:
                back_ok = bool(restart())
            except Exception:
                back_ok = False
            if not back_ok:
                raise AccessModeRollback(
                    "rollback-restart-failed",
                    "config rolled back but restart failed; service health "
                    "must be verified manually")
            raise AccessModeRejected(
                "restart-failed-rolled-back",
                "restart/health verification failed; enabled config "
                "restored and service restarted")

    # Success only now: drop managed state, and never do so during a new run.
    _require_idle(check_no_active_run)
    if _sha256_bytes(_read_file(abs_path)) != _sha256_bytes(new_bytes):
        raise AccessModeRace("config-changed-during-apply", "config changed during restart; managed receipt retained")
    _remove_receipt(sd)
    return {"status": "sandboxed", "already_restored": False}


# ------------------------------------------------------------------- status

def _configured_access_mode(cfg, agent):
    """Classify configuration only; a receipt is not live runtime evidence."""
    try:
        _helper._validate_shapes(agent)
        _helper._validate_baseline(_helper.capture_baseline(agent))
        defaults = cfg["agents"].get("defaults", {})
        if not isinstance(defaults, dict):
            raise MigrationError("invalid agents.defaults")
        sandbox = defaults.get("sandbox", {})
        if not isinstance(sandbox, dict):
            raise MigrationError("invalid agents.defaults.sandbox")
        mode = agent.get("sandbox", {}).get("mode", sandbox.get("mode", "off"))
        if mode not in ("off", "all", "non-main"):
            raise MigrationError("invalid sandbox.mode")
        tools = cfg.get("tools", {})
        if not isinstance(tools, dict) or not isinstance(tools.get("exec", {}), dict):
            raise MigrationError("invalid tools.exec")
        exec_host = agent.get("tools", {}).get("exec", {}).get(
            "host", tools.get("exec", {}).get("host", "sandbox"))
    except MigrationError:
        return "unknown", ["config-malformed"]
    if mode == "all" and exec_host == "sandbox":
        return "sandboxed", []
    mismatches = set(_managed_field_mismatches(cfg)) - {"sandbox.mode"}
    if mode == "off" and not mismatches:
        return STATUS_ENABLED, []
    return "unknown", ["sandbox-not-all" if mode == "non-main" else "partial-access-config"]


def get_status(config_path, state_dir=None):
    """Report disk configuration and managed state, never active runtime proof."""
    sd = _prepare_state_dir(state_dir or _default_state_dir())
    with _Lock(sd, exclusive=False, timeout=DEFAULT_LOCK_TIMEOUT):
        result = _status_locked(config_path, sd)
        result["runtime_verified"] = False
        return result


def _status_locked(config_path, sd):
    reasons = []
    try:
        abs_path, _mode, data, cfg = _load_config(config_path)
    except AccessModeRejected as exc:
        return {"status": "unknown", "configured_status": "unknown", "managed": False,
                "receipt_status": None, "config_sha256": None, "reasons": [exc.code]}
    h0 = _sha256_bytes(data)
    try:
        agent = _select_pixel(cfg)
    except AccessModeRejected:
        reasons.append("no-pixel-agent")
        agent = None
    receipt = None
    receipt_status = None
    try:
        receipt = _load_receipt(sd)
        if receipt is not None:
            receipt_status = receipt.get("status")
            if receipt.get("config_path") != abs_path:
                reasons.append("receipt-path-mismatch")
                receipt = None
    except AccessModeRejected:
        reasons.append("state-corrupt")
    configured, config_reasons = _configured_access_mode(cfg, agent) if agent is not None else ("unknown", [])
    reasons.extend(config_reasons)
    status = "unknown"
    if receipt is None:
        if configured == STATUS_ENABLED:
            reasons.append("managed-state-missing")
        elif configured == "sandboxed" and not reasons:
            status = "sandboxed"
    elif receipt_status == STATUS_PENDING:
        reasons.append("stale-apply-pending")
    elif agent is not None:
        if _managed_field_mismatches(cfg):
            reasons.append("already-restored" if _baseline_matches(cfg, receipt["baseline"])
                           else "managed-fields-drift")
        elif configured == STATUS_ENABLED and not reasons:
            status = STATUS_ENABLED
    return {"status": status, "configured_status": configured, "managed": receipt is not None,
            "receipt_status": receipt_status, "config_sha256": h0, "reasons": reasons}
