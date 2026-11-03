"""Durable private host-side model-transition journal (Linux-only operations).

Preserves configuration bytes and POSIX ownership/mode before mutation.
Companion to the Pixel digest journal; does not change its contract. Callers
own trusted path mapping, mutation and runtime proof; storage never restores
files or infers that a model transition has succeeded.

Protocol for a future runtime coordinator (not yet connected to activation):
hold the installation lease, inspect ``Journal.current``, and refuse a new
transaction while that record is pending. Create a new ID with original
snapshots; ``plan`` must return durably before any corresponding file write.
Enter ``applying`` before mutations and ``verifying`` after configuration is
written. Only runtime and consumer proof permits completed/commit. Recovery
keeps the same ID, refuses unknown content or metadata drift, and enters
rolling-back before restoration; proof is also required before completion.
The original snapshots remain immutable. One different target per artifact
avoids discarding intermediate states; retries needing a third state require
a separate protocol, not an unjournaled write. The current record contains
the full transaction, so discovery does not rely on a second pointer file.
"""

from __future__ import annotations

import base64
import binascii
import errno
import contextlib
import ctypes
import secrets
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Optional, NoReturn

import model_lifecycle_lock

if sys.platform.startswith("linux"):
    import fcntl

_SCHEMA_VERSION = 1
_MAX_ARTIFACTS = 64
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_MAX_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_JSON_BYTES = 48 * 1024 * 1024
_TXID_RE = re.compile(r"^[0-9a-f]{64}$")
_PHASES = ("prepared", "applying", "verifying", "rolling-back", "completed")
_OUTCOMES = (None, "commit", "rollback")
_TOP_KEYS = {"schemaVersion", "transactionId", "revision", "phase", "outcome", "before", "target"}
SnapshotMap = dict[str, Optional[dict[str, Any]]]


class _MissingJournal(FileNotFoundError):
    """Only an absent journal leaf/directory, never a failed consistency check."""


def _fail(msg: str) -> NoReturn:
    raise RuntimeError(msg)


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _require_linux() -> None:
    if not _is_linux():
        _fail("model-transition journal requires Linux")


def _b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _b64d(s: str) -> bytes:
    if not isinstance(s, str):
        _fail("snapshot data must be string")
    try:
        return base64.b64decode(s.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        _fail("invalid base64 in snapshot")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_snapshot(value):
    if type(value) is not dict or set(value) != {"data", "mode", "uid", "gid"}:
        _fail('snapshot invalid')
    data, mode, uid, gid = value["data"], value["mode"], value["uid"], value["gid"]
    if type(data) is not bytes or len(data) > _MAX_ARTIFACT_BYTES:
        _fail('snapshot invalid')
    if type(mode) is not int or not (0 <= mode <= 0o777):
        _fail('snapshot invalid')
    for value_id in (uid, gid):
        if type(value_id) is not int or not (0 <= value_id <= 4294967294):
            _fail('snapshot invalid')
    return {"data": data, "mode": mode, "uid": uid, "gid": gid}


def _validate_map(m: Any, allowed: set[str], label: str) -> SnapshotMap:
    if not isinstance(m, dict):
        _fail(f"{label} must be object")
    if set(m.keys()) != allowed:
        _fail(f"{label} keys mismatch")
    if len(m) > _MAX_ARTIFACTS:
        _fail("too many artifacts")
    total = 0
    out: SnapshotMap = {}
    for k, v in m.items():
        if not isinstance(k, str):
            _fail("artifact name must be string")
        if v is None:
            out[k] = None
            continue
        snapshot = _validate_snapshot(v)
        total += len(snapshot["data"])
        if total > _MAX_TOTAL_BYTES:
            _fail("total artifact bytes too large")
        out[k] = snapshot
    return out


def _encode_map(m: SnapshotMap) -> dict[str, Any]:
    enc: dict[str, Any] = {}
    for k, v in m.items():
        if v is None:
            enc[k] = None
        else:
            enc[k] = {**v, "data": _b64e(v["data"]), "sha256": _sha(v["data"])}
    return enc


def _decode_map(m: Any, allowed: set[str], label: str) -> SnapshotMap:
    if not isinstance(m, dict):
        _fail(f"{label} must be object")
    if set(m.keys()) != allowed:
        _fail(f"{label} keys mismatch")
    if len(m) > _MAX_ARTIFACTS:
        _fail("too many artifacts")
    total = 0
    out: SnapshotMap = {}
    for k, v in m.items():
        if v is None:
            out[k] = None
            continue
        if not isinstance(v, dict) or set(v) != {"data", "sha256", "mode", "uid", "gid"}:
            _fail("snapshot entry schema invalid")
        h = v["sha256"]
        snapshot = _validate_snapshot({"data": _b64d(v["data"]), "mode": v["mode"], "uid": v["uid"], "gid": v["gid"]})
        b = snapshot["data"]
        if not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h):
            _fail("invalid sha256")
        if _sha(b) != h:
            _fail("sha256 mismatch")
        total += len(b)
        if total > _MAX_TOTAL_BYTES:
            _fail("total artifact bytes too large")
        out[k] = snapshot
    return out


def _validate_record(rec: Any, txid: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(rec, dict):
        _fail("record must be object")
    if set(rec.keys()) != _TOP_KEYS:
        _fail("record schema keys invalid")
    if type(rec["schemaVersion"]) is not int or rec["schemaVersion"] != _SCHEMA_VERSION:
        _fail("schemaVersion invalid")
    if rec["transactionId"] != txid:
        _fail("transactionId mismatch")
    rev = rec["revision"]
    if not isinstance(rev, int) or isinstance(rev, bool) or rev < 0:
        _fail("revision invalid")
    if rec["phase"] not in _PHASES:
        _fail("phase invalid")
    if rec["outcome"] not in _OUTCOMES:
        _fail("outcome invalid")
    if (rec["phase"] == "completed") != (rec["outcome"] is not None):
        _fail("outcome/phase mismatch")
    before = _decode_map(rec["before"], allowed, "before")
    target = _decode_map(rec["target"], allowed, "target")
    _check_total(before, target)
    return {
        "schemaVersion": _SCHEMA_VERSION,
        "transactionId": txid,
        "revision": rev,
        "phase": rec["phase"],
        "outcome": rec["outcome"],
        "before": before,
        "target": target,
    }


def _check_owner_mode(st: os.stat_result, mode: int, label: str) -> None:
    if not stat.S_ISREG(st.st_mode):
        _fail(f"{label} not regular file")
    if st.st_uid != os.geteuid():
        _fail(f"{label} wrong owner")
    if stat.S_IMODE(st.st_mode) != mode:
        _fail(f"{label} wrong mode")
    if st.st_nlink != 1:
        _fail(f"{label} link count invalid")


def _read_bounded(fd: int) -> bytes:
    chunks = []
    total = 0
    while True:
        b = os.read(fd, 65536)
        if not b:
            break
        total += len(b)
        if total > _MAX_JSON_BYTES:
            _fail("record too large")
        chunks.append(b)
    return b"".join(chunks)


def _witness(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            _fail("short write")
        view = view[n:]


def _check_total(before: dict, target: dict) -> None:
    if sum(len(value["data"]) for group in (before, target) for value in group.values()
           if value is not None) > _MAX_TOTAL_BYTES:
        _fail("total artifact bytes too large")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate record key")
        result[key] = value
    return result


def _rename_exclusive(parent_fd: int, source: str, destination: str) -> None:
    # Linux's atomic no-replace operation; never emulate with link/unlink.
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        raise OSError(errno.ENOTSUP, "Atomic journal publication unavailable")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    function.restype = ctypes.c_int
    if function(parent_fd, os.fsencode(source), parent_fd, os.fsencode(destination), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class Journal:
    def __init__(self, install_dir: Path, transaction_id: str, lease_fd: int, allowed_names: set[str]):
        if not isinstance(transaction_id, str) or not _TXID_RE.fullmatch(transaction_id):
            _fail("transaction_id must be 64 lowercase hex")
        if not isinstance(allowed_names, set) or not all(isinstance(n, str) for n in allowed_names):
            _fail("allowed_names must be set of str")
        if not allowed_names or len(allowed_names) > _MAX_ARTIFACTS or any(not n or len(n) > 256 for n in allowed_names):
            _fail("too many allowed names")
        if not isinstance(lease_fd, int) or isinstance(lease_fd, bool) or lease_fd < 3:
            _fail("lease_fd invalid")
        self._install = Path(install_dir)
        self._txid = transaction_id
        self._lease_fd = lease_fd
        self._allowed = set(allowed_names)
        self._data_dir = self._install / "data"
        self._journal_dir = self._data_dir / "model-transitions"
        self._filename = "current.json"

    @property
    def transaction_id(self):
        return self._txid

    @classmethod
    def current(cls, install_dir: Path, lease_fd: int, allowed_names: set[str]):
        """Discover the sole current record; absence is distinct from damage."""
        journal = cls(install_dir, "0" * 64, lease_fd, allowed_names)
        try:
            with journal._directory() as jfd:
                rec, _ = journal._read_record(jfd, match_id=False)
        except _MissingJournal:
            return None
        journal._txid = rec["transactionId"]
        return journal

    def _check_lease(self) -> None:
        _require_linux()
        model_lifecycle_lock.validate_fd(self._install, self._lease_fd)
        try:
            fcntl.flock(self._lease_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            _fail(f"lease not held: {e.errno}")
        model_lifecycle_lock.validate_fd(self._install, self._lease_fd)

    @contextlib.contextmanager
    def _directory(self, create=False):
        self._check_lease()
        rootfd = os.open(self._install, os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        datafd = None
        childfd = None
        try:
            datafd = os.open('data', os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=rootfd)
            for fd, label in ((rootfd, 'root'), (datafd, 'data')):
                st = os.fstat(fd)
                if st.st_uid != os.geteuid():
                    raise RuntimeError(label + ' directory owner mismatch')
                if not stat.S_ISDIR(st.st_mode):
                    raise RuntimeError(label + ' not a directory')
            for fd, label in ((rootfd, 'root'), (datafd, 'data')):
                st = os.fstat(fd)
                if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                    raise RuntimeError(label + ' directory writable by group/other')
            try:
                childfd = os.open('model-transitions', os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=datafd)
            except FileNotFoundError:
                if not create:
                    # Absence is safe to report only through the held parents.
                    for parent, name, held in ((None, self._install, rootfd), (rootfd, 'data', datafd)):
                        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
                        opened = os.fstat(held)
                        if (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino):
                            _fail('journal parent binding changed')
                    raise _MissingJournal('private directory missing')
                os.mkdir('model-transitions', 0o700, dir_fd=datafd)
                os.fsync(datafd)
                childfd = os.open('model-transitions', os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=datafd)
            st = os.fstat(childfd)
            if st.st_uid != os.geteuid() or stat.S_IMODE(st.st_mode) != 0o700:
                raise RuntimeError('private directory mode mismatch')
            def _verify():
                rst = os.stat(self._install, follow_symlinks=False)
                hst = os.fstat(rootfd)
                if not stat.S_ISDIR(rst.st_mode) or (rst.st_dev, rst.st_ino) != (hst.st_dev, hst.st_ino):
                    raise RuntimeError('root directory binding changed')
                dst = os.stat('data', dir_fd=rootfd, follow_symlinks=False)
                hd = os.fstat(datafd)
                if not stat.S_ISDIR(dst.st_mode) or (dst.st_dev, dst.st_ino) != (hd.st_dev, hd.st_ino):
                    raise RuntimeError('data directory binding changed')
                cst = os.stat('model-transitions', dir_fd=datafd, follow_symlinks=False)
                hc = os.fstat(childfd)
                if not stat.S_ISDIR(cst.st_mode) or (cst.st_dev, cst.st_ino) != (hc.st_dev, hc.st_ino):
                    raise RuntimeError('private directory binding changed')
            _verify()
            try:
                yield childfd
            finally:
                _verify()
        finally:
            for fd in (childfd, datafd, rootfd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

    def _check_directory_binding(self, held_fd):
        with self._directory() as current_fd:
            current, held = os.fstat(current_fd), os.fstat(held_fd)
            if (current.st_dev, current.st_ino) != (held.st_dev, held.st_ino):
                _fail("journal directory changed")

    def _read_record(self, jfd: int, *, match_id: bool = True) -> tuple[dict[str, Any], os.stat_result]:
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
        try:
            fd = os.open(self._filename, flags, dir_fd=jfd)
        except FileNotFoundError as exc:
            raise _MissingJournal("record missing") from exc
        except OSError as e:
            _fail(f"record open failed: {e.errno}")
        try:
            st = os.fstat(fd)
            _check_owner_mode(st, 0o600, "record")
            raw = _read_bounded(fd)
            after = os.fstat(fd)
            named = os.stat(self._filename, dir_fd=jfd, follow_symlinks=False)
            _check_owner_mode(after, 0o600, "record")
            if _witness(st) != _witness(after) or _witness(named) != _witness(after):
                _fail("record changed while reading")
        finally:
            os.close(fd)
        try:
            rec = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError):
            _fail("record JSON invalid")
        txid = rec.get("transactionId") if isinstance(rec, dict) else None
        if not isinstance(txid, str) or not _TXID_RE.fullmatch(txid):
            _fail("transactionId invalid")
        return _validate_record(rec, self._txid if match_id else txid, self._allowed), st

    def _encode_record(self, rec: dict[str, Any]) -> bytes:
        obj = {
            "schemaVersion": _SCHEMA_VERSION,
            "transactionId": self._txid,
            "revision": rec["revision"],
            "phase": rec["phase"],
            "outcome": rec["outcome"],
            "before": _encode_map(rec["before"]),
            "target": _encode_map(rec["target"]),
        }
        return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def _publish(self, directory_fd, raw, expected=None):
        self._check_directory_binding(directory_fd)
        tmpname = '.tmp-' + secrets.token_hex(16)
        tmpfd = None
        staged = None
        try:
            tmpfd = os.open(tmpname, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_WRONLY, 0o600, dir_fd=directory_fd)
            staged = os.fstat(tmpfd)
            _write_all(tmpfd, raw)
            os.fchmod(tmpfd, 0o600)
            os.fsync(tmpfd)
            os.close(tmpfd)
            tmpfd = None
            self._check_directory_binding(directory_fd)
            if expected is None:
                _rename_exclusive(directory_fd, tmpname, self._filename)
            else:
                cur = os.stat(self._filename, dir_fd=directory_fd, follow_symlinks=False)
                _check_owner_mode(cur, 0o600, 'destination')
                if not stat.S_ISREG(cur.st_mode) or cur.st_nlink != 1:
                    raise RuntimeError('destination not regular single-link')
                if (cur.st_dev, cur.st_ino, cur.st_size, cur.st_mtime_ns, cur.st_ctime_ns) != (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns, expected.st_ctime_ns):
                    raise RuntimeError('destination changed')
                os.replace(tmpname, self._filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            pub = os.stat(self._filename, dir_fd=directory_fd, follow_symlinks=False)
            _check_owner_mode(pub, 0o600, 'published record')
            if not stat.S_ISREG(pub.st_mode) or pub.st_nlink != 1:
                raise RuntimeError('published file not regular single-link')
            if (pub.st_dev, pub.st_ino) != (staged.st_dev, staged.st_ino):
                raise RuntimeError('published inode mismatch')
            os.fsync(directory_fd)
        except BaseException:
            if tmpfd is not None:
                try:
                    os.close(tmpfd)
                except OSError:
                    pass
            try:
                cur = os.stat(tmpname, dir_fd=directory_fd, follow_symlinks=False)
                if staged is not None and (cur.st_dev, cur.st_ino) == (staged.st_dev, staged.st_ino):
                    os.unlink(tmpname, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            raise

    def create(self, before: SnapshotMap, target: SnapshotMap) -> dict[str, Any]:
        self._check_lease()
        b = _validate_map(before, self._allowed, "before")
        t = _validate_map(target, self._allowed, "target")
        _check_total(b, t)
        rec = {
            "schemaVersion": _SCHEMA_VERSION,
            "transactionId": self._txid,
            "revision": 0,
            "phase": "prepared",
            "outcome": None,
            "before": b,
            "target": t,
        }
        data = self._encode_record(rec)
        if len(data) > _MAX_JSON_BYTES:
            _fail("record too large")
        with self._directory(create=True) as jfd:
            try:
                existing, witness = self._read_record(jfd, match_id=False)
            except _MissingJournal:
                witness = None
            else:
                if existing["phase"] != "completed":
                    raise FileExistsError("A model transition requires recovery before another can begin")
                if existing["transactionId"] == self._txid:
                    raise FileExistsError("A model transition ID cannot be reused")
            self._publish(jfd, data, expected=witness)
        return rec

    def load(self) -> dict[str, Any]:
        self._check_lease()
        with self._directory() as jfd:
            rec, _ = self._read_record(jfd)
        return rec

    def plan(self, expected_revision: int, updates: SnapshotMap) -> dict[str, Any]:
        """Persist write intents, retaining every original recovery snapshot.

        Each artifact gets at most one target distinct from its original state.
        A second different target would discard an intermediate state which a
        crash might leave on disk. Callers must not perform that second write.
        """
        self._check_lease()
        if type(expected_revision) is not int or expected_revision < 0:
            _fail("expected_revision invalid")
        if not isinstance(updates, dict) or not updates or not set(updates) <= self._allowed:
            _fail("write intents contain invalid names")
        validated = _validate_map(updates, set(updates), "write intents")
        with self._directory() as jfd:
            rec, st = self._read_record(jfd)
            if rec["revision"] != expected_revision:
                _fail("revision mismatch")
            if rec["phase"] not in ("prepared", "applying"):
                _fail("write intents cannot change during verification or recovery")
            target = dict(rec["target"])
            for name, value in validated.items():
                if target[name] != rec["before"][name] and value != target[name]:
                    _fail("artifact already has a different write intent")
                target[name] = value
            _check_total(rec["before"], target)
            new = {**rec, "revision": rec["revision"] + 1, "target": target}
            data = self._encode_record(new)
            if len(data) > _MAX_JSON_BYTES:
                _fail("record too large")
            self._publish(jfd, data, expected=st)
        return new

    def checkpoint(self, expected_revision: int, phase: str, outcome: Optional[str] = None) -> dict[str, Any]:
        self._check_lease()
        if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
            _fail("expected_revision invalid")
        if phase not in _PHASES:
            _fail("phase invalid")
        if outcome not in _OUTCOMES:
            _fail("outcome invalid")
        with self._directory() as jfd:
            rec, st = self._read_record(jfd)
            if rec["revision"] != expected_revision:
                _fail("revision mismatch")
            cur = rec["phase"]
            legal = {
                "prepared": {"applying", "rolling-back"},
                "applying": {"verifying", "rolling-back"},
                "verifying": {"completed", "rolling-back"},
                "rolling-back": {"completed"},
                "completed": set(),
            }
            if phase not in legal[cur]:
                _fail("illegal phase transition")
            if phase == "completed":
                if cur == "verifying" and outcome != "commit":
                    _fail("verifying completion requires commit")
                if cur == "rolling-back" and outcome != "rollback":
                    _fail("rolling-back completion requires rollback")
            else:
                if outcome is not None:
                    _fail("outcome only allowed on completion")
            new = dict(rec)
            new["revision"] = rec["revision"] + 1
            new["phase"] = phase
            new["outcome"] = outcome
            data = self._encode_record(new)
            if len(data) > _MAX_JSON_BYTES:
                _fail("record too large")
            self._publish(jfd, data, expected=st)
        return new

    def classify(self, current: SnapshotMap) -> dict[str, str]:
        self._check_lease()
        cur = _validate_map(current, self._allowed, "current")
        with self._directory() as jfd:
            rec, _ = self._read_record(jfd)
        out: dict[str, str] = {}
        for name in self._allowed:
            c = cur[name]
            b = rec["before"][name]
            t = rec["target"][name]
            if c == b and c == t:
                out[name] = "both"
            elif c == b:
                out[name] = "before"
            elif c == t:
                out[name] = "target"
            else:
                out[name] = "drift"
        return out
