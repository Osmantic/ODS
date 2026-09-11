"""Durable single-process admission gate. This does not verify host runtime idle.

Provision state once, offline: python3 transition_gate.py --initialize /state
The directory must already exist, be mode 0700, and belong to the edge UID.
"""

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import stat

try:
    import fcntl
except ImportError:  # The capability is explicitly unavailable outside POSIX.
    fcntl = None


_HEX = re.compile(r"[0-9a-f]{64}\Z")
_STATE = "transition.json"
_LOCK = "transition.lock"


class GateError(Exception):
    def __init__(self, reason, status=503):
        self.reason = reason
        self.status = status
        super().__init__(reason)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate property")
        result[key] = value
    return result


def strict_json(raw):
    return json.loads(raw, object_pairs_hook=_unique_object)


def valid_binding(value):
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _empty_state():
    return {"version": 1, "phase": "idle", "revision": secrets.token_hex(32),
            "token_hash": None, "released": None}


class TransitionGate:
    def __init__(self, path, active, *, owner_key_distinct=True):
        self.path = path
        self.active = active
        self.mutex = asyncio.Lock()
        self.dir_fd = None
        self.lock_fd = None
        self.state = None
        self.failure = None
        self.closing = False
        if not path:
            return
        try:
            if not owner_key_distinct:
                raise GateError("owner_credential_not_distinct")
            self._open()
            self.state = self._read()
            if self.state["phase"] == "busy":
                self._save({**self.state, "phase": "interrupted"})
        except GateError as exc:
            self.failure = exc.reason
            self.close()
        except (OSError, ValueError, RecursionError):
            self.failure = "durable_state_unavailable"
            self.close()

    @staticmethod
    def _owned(info, kind, mode):
        if (not kind(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != mode):
            raise GateError("unsafe_state_permissions")

    def _open(self):
        if fcntl is None or not os.path.isabs(self.path):
            raise GateError("durable_state_unsupported")
        if os.path.realpath(self.path) != os.path.normpath(self.path):
            raise GateError("unsafe_state_directory")
        self.dir_fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self._owned(os.fstat(self.dir_fd), stat.S_ISDIR, 0o700)
        self.lock_fd = os.open(_LOCK, os.O_RDWR | os.O_NOFOLLOW, dir_fd=self.dir_fd)
        self._owned(os.fstat(self.lock_fd), stat.S_ISREG, 0o600)
        if os.fstat(self.lock_fd).st_nlink != 1:
            raise GateError("unsafe_state_lock")
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GateError("state_already_in_use") from exc

    def _directory_unchanged(self):
        current = os.stat(self.path, follow_symlinks=False)
        self._owned(current, stat.S_ISDIR, 0o700)
        opened = os.fstat(self.dir_fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise GateError("state_directory_changed")
        lock = os.stat(_LOCK, dir_fd=self.dir_fd, follow_symlinks=False)
        held = os.fstat(self.lock_fd)
        self._owned(lock, stat.S_ISREG, 0o600)
        if (lock.st_dev, lock.st_ino, lock.st_nlink) != (held.st_dev, held.st_ino, 1):
            raise GateError("state_lock_changed")

    def _read(self):
        self._directory_unchanged()
        fd = os.open(_STATE, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=self.dir_fd)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            self._owned(info, stat.S_ISREG, 0o600)
            if info.st_nlink != 1 or info.st_size > 2048:
                raise GateError("unsafe_state_file")
            data = strict_json(stream.read(2049))
        if (not isinstance(data, dict)
                or set(data) != {"version", "phase", "revision", "token_hash", "released"}
                or type(data["version"]) is not int or data["version"] != 1
                or data["phase"] not in ("idle", "busy", "held", "interrupted")
                or not valid_binding(data["revision"])):
            raise ValueError("invalid state")
        held = data["phase"] == "held"
        if (held and not valid_binding(data["token_hash"])) or (not held and data["token_hash"] is not None):
            raise ValueError("invalid state binding")
        released = data["released"]
        if released is not None and (
                data["phase"] != "idle" or not isinstance(released, dict)
                or set(released) != {"revision", "token_hash"}
                or not all(valid_binding(v) for v in released.values())):
            raise ValueError("invalid release receipt")
        return data

    def _save(self, state):
        # Synchronous, bounded IO stays inside the admission mutex. No task
        # cancellation may split durable admission from the in-process counter.
        name = ".transition-" + secrets.token_hex(16)
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.dir_fd)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(state, sort_keys=True).encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            self._directory_unchanged()
            os.replace(name, _STATE, src_dir_fd=self.dir_fd, dst_dir_fd=self.dir_fd)
            os.fsync(self.dir_fd)
            self.state = state
        finally:
            try:
                os.unlink(name, dir_fd=self.dir_fd)
            except FileNotFoundError:
                pass

    def _check(self):
        if not self.path:
            raise GateError("durable_state_not_configured")
        if self.failure:
            raise GateError(self.failure)
        try:
            if self._read() != self.state:
                raise GateError("state_changed_outside_edge")
        except (OSError, ValueError, RecursionError, GateError) as exc:
            self.failure = "durable_state_unavailable"
            raise GateError(self.failure) from exc

    def _write(self, state):
        try:
            self._save(state)
        except (OSError, ValueError, GateError) as exc:
            self.failure = "durable_state_unavailable"
            raise GateError(self.failure) from exc

    def _status(self):
        return {"capability": "available", "phase": self.state["phase"],
                "revision": self.state["revision"], "streams": len(self.active),
                "admission_blocked": self.closing or self.state["phase"] in ("held", "interrupted"),
                "host_runtime_verified": False}

    async def status(self):
        async with self.mutex:
            try:
                self._check()
            except GateError as exc:
                return {"capability": "unavailable" if self.path else "disabled",
                        "reason": exc.reason, "admission_blocked": bool(self.path),
                        "streams": len(self.active), "host_runtime_verified": False}
            return self._status()

    async def acquire(self, token, revision, *, recover=False):
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        async with self.mutex:
            self._check()
            if self.closing:
                raise GateError("edge_shutting_down")
            if revision != self.state["revision"]:
                raise GateError("revision_conflict", 409)
            if self.state["phase"] == "held":
                if hmac.compare_digest(token_hash, self.state["token_hash"]):
                    return self._status()
                raise GateError("transition_already_held", 409)
            if self.active or self.state["phase"] == "busy":
                raise GateError("active_turns", 409)
            if self.state["phase"] == "interrupted" and not recover:
                raise GateError("interrupted_turn_requires_recovery", 409)
            if recover and self.state["phase"] != "interrupted":
                raise GateError("recovery_not_required", 409)
            self._write({**self.state, "phase": "held", "token_hash": token_hash, "released": None})
            return self._status()

    async def release(self, token, revision):
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        async with self.mutex:
            self._check()
            receipt = self.state["released"]
            if (self.state["phase"] == "idle" and receipt
                    and receipt["revision"] == revision
                    and hmac.compare_digest(receipt["token_hash"], token_hash)):
                return self._status()
            if revision != self.state["revision"]:
                raise GateError("revision_conflict", 409)
            if self.state["phase"] != "held":
                raise GateError("transition_not_held", 409)
            if not hmac.compare_digest(self.state["token_hash"], token_hash):
                raise GateError("transition_token_mismatch", 409)
            if self.active or self.closing:
                raise GateError("edge_not_idle", 409)
            state = _empty_state()
            state["released"] = {"revision": revision, "token_hash": token_hash}
            self._write(state)
            return self._status()

    async def admit(self, request_token):
        async with self.mutex:
            if self.closing:
                raise GateError("edge_shutting_down")
            if self.path:
                self._check()
                if self.state["phase"] in ("held", "interrupted"):
                    raise GateError("pixel_transition_in_progress", 409)
                if not self.active:
                    self._write({**_empty_state(), "phase": "busy"})
            self.active.add(request_token)

    async def finish(self, request_token):
        async with self.mutex:
            self.active.discard(request_token)
            if self.path and not self.active and not self.closing and not self.failure:
                try:
                    self._check()
                    if self.state["phase"] == "busy":
                        self._write(_empty_state())
                except GateError:
                    pass  # Sticky failure blocks all later admissions/acquisitions.

    async def shutdown(self):
        async with self.mutex:
            self.closing = True
            if self.path and not self.failure and self.state["phase"] == "busy":
                try:
                    self._check()
                    self._write({**self.state, "phase": "interrupted"})
                except GateError:
                    pass

    def close(self):
        for attr in ("lock_fd", "dir_fd"):
            fd = getattr(self, attr)
            if fd is not None:
                os.close(fd)
                setattr(self, attr, None)


def initialize(path):
    """Explicit first-install provisioning; never overwrite existing state."""
    if fcntl is None or not os.path.isabs(path) or os.path.realpath(path) != os.path.normpath(path):
        raise GateError("unsafe_state_directory")
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        TransitionGate._owned(os.fstat(directory), stat.S_ISDIR, 0o700)
        lock = os.open(_LOCK, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                       0o600, dir_fd=directory)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fd = os.open(_STATE, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(json.dumps(_empty_state(), sort_keys=True).encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
            os.fsync(directory)
        finally:
            os.close(lock)
    finally:
        os.close(directory)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--initialize", required=True)
    args = parser.parse_args()
    try:
        initialize(args.initialize)
    except (OSError, ValueError, GateError):
        parser.exit(1, "Transition state initialization refused; inspect the owner directory offline.\n")
