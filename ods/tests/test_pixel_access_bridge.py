"""Contract tests for bin/pixel_access_bridge.py.

The root-owned access bridge is the concrete adapter behind every Pixel
control-plane transaction — the model coordinator's begin/apply/finish flow
rides on its ``worker``/``native``/``edge`` frames, and the settings/provider
coordinators share the same custody and deadline machinery.

These tests pin the serialization, custody, and validation surface that runs
BEFORE any systemd/docker I/O: canonical digests, the deadline contextvar,
non-blocking pipe framing, owner-state file rules, the docker-exec request
allowlist, the loopback/private-only HTTP gate, and the durable model journal
and completion records.
"""
import importlib.util
import json
import os
import re
import socket
import stat
import sys
import time
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))

import pixel_access_bridge as bridge  # noqa: E402
from pixel_access_bridge import AccessError, SystemdAccessBridge  # noqa: E402


def make_bridge(tmp_path, **kwargs):
    kwargs.setdefault("state", tmp_path / "state")
    kwargs.setdefault("dropin", tmp_path / "dropin" / "90-ods-full-access.conf")
    return SystemdAccessBridge(tmp_path / "install", "k" * 64, **kwargs)


def write_private(path, payload, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    os.chmod(path, mode)
    return path


class TestDigest:
    def test_canonical_key_order(self):
        a = bridge.digest({"b": 1, "a": 2})
        b = bridge.digest({"a": 2, "b": 1})
        assert a == b and re.fullmatch(r"[a-f0-9]{64}", a)

    def test_compact_separators(self):
        assert bridge.digest({"a": [1, 2]}) == bridge.digest(
            json.loads('{"a":[1,2]}'))

    def test_distinct_values_differ(self):
        assert bridge.digest({"a": 1}) != bridge.digest({"a": 2})


class TestDeadline:
    def test_remaining_without_deadline_returns_timeout(self):
        assert bridge.remaining(30) == 30

    def test_bounded_caps_remaining(self, tmp_path):
        b = make_bridge(tmp_path)
        with b.bounded(100):
            assert bridge.remaining(50) == 50
            assert bridge.remaining(10_000) <= 100

    def test_bounded_rejects_expired_deadline(self, tmp_path):
        b = make_bridge(tmp_path)
        with pytest.raises(AccessError, match="operation-deadline"):
            with b.bounded(0):
                pass

    def test_bounded_resets_contextvar(self, tmp_path):
        b = make_bridge(tmp_path)
        with b.bounded(30):
            pass
        assert bridge.remaining(7) == 7

    def test_nested_bounded_uses_inner_deadline(self, tmp_path):
        b = make_bridge(tmp_path)
        with b.bounded(500):
            with b.bounded(60):
                assert bridge.remaining(1000) <= 60


class TestPipeSend:
    def test_writes_full_frame(self):
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(write_fd, "wb") as stream:
                bridge._pipe_send(
                    stream, "payload", time.monotonic() + 10)
            assert os.read(read_fd, 64) == b"payload"
        finally:
            os.close(read_fd)

    def test_deadline_expiry_raises(self):
        read_fd, write_fd = os.pipe()
        try:
            with os.fdopen(write_fd, "wb") as stream:
                with pytest.raises(AccessError):
                    bridge._pipe_send(
                        stream, "x" * 4096, time.monotonic() - 1)
        finally:
            os.close(read_fd)


class TestPrivateJson:
    def test_reads_valid_owner_file(self, tmp_path):
        target = write_private(tmp_path / "state.json", {"k": 1})
        assert bridge.private_json(target, os.geteuid()) == {"k": 1}

    def test_rejects_world_readable(self, tmp_path):
        target = write_private(tmp_path / "s.json", {}, 0o644)
        with pytest.raises(AccessError, match="unsafe-owner-state"):
            bridge.private_json(target, os.geteuid())

    def test_rejects_symlink(self, tmp_path):
        real = write_private(tmp_path / "real.json", {})
        link = tmp_path / "link.json"
        link.symlink_to(real)
        # O_NOFOLLOW makes the open itself fail — the file is never read.
        with pytest.raises(OSError):
            bridge.private_json(link, os.geteuid())

    def test_rejects_wrong_uid(self, tmp_path):
        target = write_private(tmp_path / "s.json", {})
        with pytest.raises(AccessError, match="unsafe-owner-state"):
            bridge.private_json(target, os.geteuid() + 1)

    def test_rejects_oversized(self, tmp_path):
        target = write_private(tmp_path / "s.json", {"pad": "x" * 5000})
        with pytest.raises(AccessError, match="unsafe-owner-state"):
            bridge.private_json(target, os.geteuid(), maximum=1024)

    def test_rejects_directory(self, tmp_path):
        with pytest.raises(AccessError, match="unsafe-owner-state"):
            bridge.private_json(tmp_path, os.geteuid())

    def test_rejects_hardlink(self, tmp_path):
        target = write_private(tmp_path / "s.json", {})
        os.link(target, tmp_path / "dup.json")
        with pytest.raises(AccessError, match="unsafe-owner-state"):
            bridge.private_json(tmp_path / "dup.json", os.geteuid())


class TestAtomicJson:
    def test_round_trip_and_no_temp_left(self, tmp_path):
        target = tmp_path / "transition.json"
        bridge.atomic_json(target, {"phase": "held", "n": 3})
        assert json.loads(target.read_text()) == {"phase": "held", "n": 3}
        assert not list(tmp_path.glob(".transition-*"))

    def test_replaces_existing_atomically(self, tmp_path):
        target = tmp_path / "t.json"
        bridge.atomic_json(target, {"v": 1})
        bridge.atomic_json(target, {"v": 2})
        assert json.loads(target.read_text()) == {"v": 2}


class TestEdgeJson:
    def test_dict_parsed(self):
        assert bridge._edge_json(b'{"a": 1}') == {"a": 1}

    @pytest.mark.parametrize("raw", [
        b'{"a": 1, "a": 2}',       # duplicate key
        b'{"a": NaN}',             # invalid constant
        b'{"a": Infinity}',
        b'[1, 2]',                 # not a dict
        b'"x"',
        b'not json',
    ])
    def test_rejected(self, raw):
        with pytest.raises(ValueError):
            bridge._edge_json(raw)


class TestEdgeContainerValidation:
    """Every check below runs before docker exec is spawned."""

    CID = "a" * 64
    KEY = "k" * 64
    TOKREV = {"token": "b" * 64, "revision": "c" * 64}

    def test_transition_probe_accepts_none_payload(self, monkeypatch):
        spawned = []
        monkeypatch.setattr(bridge.subprocess, "Popen",
                            lambda *a, **k: spawned.append(a) or (_ for _ in ()).throw(RuntimeError))
        with pytest.raises(RuntimeError):
            bridge._edge_container_request(self.CID, "/v1/transition", self.KEY, None)
        assert spawned  # reached Popen — validation passed

    @pytest.mark.parametrize("cid", ["short", "A" * 64, "g" * 64, 123, None])
    def test_bad_container_id(self, cid):
        with pytest.raises(AccessError, match="edge-container-unavailable"):
            bridge._edge_container_request(cid, "/v1/transition", self.KEY, None)

    @pytest.mark.parametrize("path", ["/v1/other", "/admin", "", "//v1/transition"])
    def test_bad_path(self, path):
        with pytest.raises(AccessError, match="edge-container-unavailable"):
            bridge._edge_container_request(self.CID, path, self.KEY, None)

    @pytest.mark.parametrize("key", [
        "short", "k" * 31, "k" * 4097, "key with space", "key\ninject", "kéy", 123,
    ])
    def test_bad_key(self, key):
        with pytest.raises(AccessError, match="edge-owner-auth-unavailable"):
            bridge._edge_container_request(self.CID, "/v1/transition", key, None)

    @pytest.mark.parametrize("path,payload,ok", [
        ("/v1/transition", None, True),
        ("/v1/transition", {"token": "b" * 64}, False),
        ("/v1/transition/acquire", TOKREV, True),
        ("/v1/transition/recover", TOKREV, True),
        ("/v1/transition/release", TOKREV, True),
        ("/v1/transition/acquire", None, False),
        ("/v1/transition/acquire", {"token": "b" * 64}, False),
        ("/v1/transition/acquire", dict(TOKREV, extra="x"), False),
        ("/v1/transition/acquire", {"token": "B" * 64, "revision": "c" * 64}, False),
        ("/v1/transition/acquire", {"token": "b" * 64, "revision": "short"}, False),
    ])
    def test_payload_shape(self, path, payload, ok, monkeypatch):
        monkeypatch.setattr(bridge.subprocess, "Popen",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError))
        if ok:
            with pytest.raises(RuntimeError):
                bridge._edge_container_request(self.CID, path, self.KEY, payload)
        else:
            with pytest.raises(AccessError, match="invalid-edge-operation"):
                bridge._edge_container_request(self.CID, path, self.KEY, payload)

    @pytest.mark.parametrize("timeout", [0, -1, 21, "x", None])
    def test_bad_timeout(self, timeout):
        with pytest.raises(AccessError, match="operation-deadline-exceeded"):
            bridge._edge_container_request(
                self.CID, "/v1/transition", self.KEY, None, timeout=timeout)


class TestBridgeInit:
    def test_valid_port(self, tmp_path):
        assert make_bridge(tmp_path, gateway_port=8080).gateway_port == 8080

    @pytest.mark.parametrize("port", [0, 65536, "8080", True, -1])
    def test_invalid_port(self, tmp_path, port):
        with pytest.raises(AccessError, match="gateway-port-unavailable"):
            make_bridge(tmp_path, gateway_port=port)


class TestConfiguredGatewayPort:
    def test_override_wins(self, tmp_path):
        b = make_bridge(tmp_path, gateway_port=9090)
        assert b.configured_gateway_port({"gateway": {"port": 1}}) == 9090

    def test_config_default(self, tmp_path):
        b = make_bridge(tmp_path)
        assert b.configured_gateway_port({"gateway": {"port": 1234}}) == 1234

    def test_builtin_default(self, tmp_path):
        assert make_bridge(tmp_path).configured_gateway_port({}) == 18789

    @pytest.mark.parametrize("port", [0, 65536, "x", True, 1.5])
    def test_invalid_config_port(self, tmp_path, port):
        b = make_bridge(tmp_path)
        with pytest.raises(AccessError, match="gateway-auth-unavailable"):
            b.configured_gateway_port({"gateway": {"port": port}})


class TestHttpGate:
    """Origin/path validation runs before any connection attempt."""

    def _call(self, b, **kw):
        args = dict(origin="http://127.0.0.1:1", path="/health",
                    key="k" * 64, payload=None, timeout=1)
        args.update(kw)
        return b.http(**args)

    @pytest.mark.parametrize("origin", [
        "https://127.0.0.1:1",           # scheme must be http
        "http://example.com",            # public address
        "http://8.8.8.8:1",
        "http://user@127.0.0.1:1",
        "http://127.0.0.1:1?x=1",
        "http://127.0.0.1:1#f",
        "http://127.0.0.1:1/sub",
        "http://not-an-ip:1",
    ])
    def test_origin_rejected(self, tmp_path, origin):
        with pytest.raises(AccessError, match="invalid-service-origin"):
            self._call(make_bridge(tmp_path), origin=origin)

    @pytest.mark.parametrize("origin", [
        "http://127.0.0.1:1", "http://[::1]:1", "http://10.0.0.1:1",
        "http://192.168.1.5:1",
    ])
    def test_loopback_and_private_pass_gate(self, tmp_path, origin):
        # Reach connection attempt — refused fast since nothing listens.
        with pytest.raises(AccessError,
                           match="runtime-unavailable-or-busy|runtime-operation-timeout"):
            self._call(make_bridge(tmp_path), origin=origin)

    @pytest.mark.parametrize("path", ["/admin", "/v1/chat/completions", ""])
    def test_path_allowlist(self, tmp_path, path):
        with pytest.raises(AccessError, match="invalid-service-origin"):
            self._call(make_bridge(tmp_path), path=path)

    def test_key_with_control_chars_rejected(self, tmp_path):
        with pytest.raises(AccessError, match="invalid-service-auth"):
            self._call(make_bridge(tmp_path), key="bad\nkey")


class TestEdgeOperationGate:
    @pytest.mark.parametrize("op", ["destroy", "", "ACQUIRE", "acquire;rm"])
    def test_invalid_operation_rejected(self, tmp_path, op):
        with pytest.raises(AccessError, match="invalid-edge-operation"):
            make_bridge(tmp_path).edge(op, "t" * 64, "r" * 64)


class TestModelJournal:
    def _journal(self, **kw):
        doc = {
            "kind": "model",
            "transaction_id": "a" * 64,
            "token": "b" * 64,
            "phase": "held",
            "edge_revision": "c" * 64,
            "configured_mode": "sandboxed",
            "start_config_sha256": "d" * 64,
        }
        doc.update(kw)
        return doc

    def _pending_bridge(self, tmp_path, journal):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        write_private(b.state / "transition.json", journal)
        return b

    def test_valid_journal(self, tmp_path):
        b = self._pending_bridge(tmp_path, self._journal())
        assert b.model_journal()["transaction_id"] == "a" * 64

    def test_journal_with_error(self, tmp_path):
        b = self._pending_bridge(
            tmp_path, self._journal(phase="error", error="runtime-busy"))
        assert b.model_journal()["error"] == "runtime-busy"

    def test_matching_transaction_id(self, tmp_path):
        b = self._pending_bridge(tmp_path, self._journal())
        assert b.model_journal("a" * 64)["phase"] == "held"

    def test_mismatched_transaction_id(self, tmp_path):
        b = self._pending_bridge(tmp_path, self._journal())
        with pytest.raises(AccessError, match="model-transaction-mismatch"):
            b.model_journal("e" * 64)

    @pytest.mark.parametrize("mutation", [
        {"kind": "access"},
        {"transaction_id": "short"},
        {"transaction_id": "A" * 64},
        {"token": "x"},
        {"edge_revision": "x"},
        {"configured_mode": "full-access!"},
        {"configured_mode": "unknown"},
        {"start_config_sha256": "x"},
        {"phase": "unknown"},
        {"phase": "committed"},
        {"extra_field": 1},
        {"error": "Bad Error"},
        {"error": "x" * 97},  # regex allows first char + up to 95 more
        {"error": 5},
    ])
    def test_invalid_journal_rejected(self, tmp_path, mutation):
        b = self._pending_bridge(tmp_path, self._journal(**mutation))
        with pytest.raises(AccessError, match="model-recovery-required"):
            b.model_journal()

    @pytest.mark.parametrize("phase", [
        "acquiring", "draining", "held", "finishing",
        "releasing", "native-released", "error",
    ])
    def test_all_phases_accepted(self, tmp_path, phase):
        b = self._pending_bridge(tmp_path, self._journal(phase=phase))
        assert b.model_journal()["phase"] == phase

    def test_model_status_shape(self, tmp_path):
        b = self._pending_bridge(
            tmp_path, self._journal(phase="error", error="runtime-busy"))
        status = b.model_status()
        assert status == {
            "pending": True, "kind": "model",
            "transaction_id": "a" * 64, "phase": "error",
            "configured_mode": "sandboxed",
            "start_config_sha256": "d" * 64, "error": "runtime-busy",
        }

    def test_model_status_no_pending(self, tmp_path):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        assert b.model_status() == {"pending": False}

    def test_model_error_records_code(self, tmp_path):
        b = self._pending_bridge(tmp_path, self._journal())
        b.model_error(b.model_journal(), AccessError("runtime-busy"))
        doc = json.loads((b.state / "transition.json").read_text())
        assert doc["phase"] == "error" and doc["error"] == "runtime-busy"

    def test_model_error_generic_becomes_transition_failed(self, tmp_path):
        b = self._pending_bridge(tmp_path, self._journal())
        b.model_error(b.model_journal(), RuntimeError("boom"))
        doc = json.loads((b.state / "transition.json").read_text())
        assert doc["error"] == "model-transition-failed"


class TestModelCompletion:
    def _completion(self, **kw):
        doc = {
            "kind": "model-completion",
            "transaction_id": "a" * 64,
            "outcome": "applied",
            "config_sha256": "d" * 64,
        }
        doc.update(kw)
        return doc

    def _bridge(self, tmp_path, doc):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        write_private(b.state / "model-completed.json", doc)
        return b

    def test_valid_completion(self, tmp_path):
        b = self._bridge(tmp_path, self._completion())
        assert b.model_completion()["outcome"] == "applied"

    def test_missing_file_returns_none(self, tmp_path):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        assert b.model_completion() is None

    @pytest.mark.parametrize("mutation", [
        {"kind": "model"},
        {"transaction_id": "x"},
        {"outcome": "committed"},
        {"outcome": "applied", "extra": 1},
        {"config_sha256": "z"},
        {"kind": "model-completion", "outcome": "applied",
         "config_sha256": "d" * 64},  # missing transaction_id
    ])
    def test_invalid_completion_rejected(self, tmp_path, mutation):
        doc = self._completion()
        doc.pop("transaction_id", None)
        doc.update(mutation)
        b = self._bridge(tmp_path, doc)
        with pytest.raises(AccessError, match="model-recovery-required"):
            b.model_completion()

    def test_request_mismatch_returns_none(self, tmp_path):
        b = self._bridge(tmp_path, self._completion())
        assert b.model_completion(
            {"transaction_id": "e" * 64, "outcome": "applied"}) is None
        assert b.model_completion(
            {"transaction_id": "a" * 64, "outcome": "rolled-back"}) is None

    def test_request_match_returns_doc(self, tmp_path):
        b = self._bridge(tmp_path, self._completion())
        assert b.model_completion(
            {"transaction_id": "a" * 64, "outcome": "applied"})["kind"] == (
            "model-completion")


class TestRequestValidation:
    """model_finish / change reject malformed requests before any I/O."""

    @pytest.mark.parametrize("req", [
        None, "x", {},
        {"transaction_id": "a" * 64},
        {"transaction_id": "a" * 64, "outcome": "applied", "extra": 1},
        {"transaction_id": "short", "outcome": "applied"},
        {"transaction_id": "a" * 64, "outcome": "committed"},
        {"transaction_id": 5, "outcome": "applied"},
    ])
    def test_model_finish_invalid(self, tmp_path, req):
        with pytest.raises(AccessError, match="invalid-request"):
            make_bridge(tmp_path).model_finish(req)

    @pytest.mark.parametrize("req", [
        None, {},
        {"mode": "full-access", "revision": "a" * 64, "confirmed": True,
         "extra": 1},
        {"mode": "off", "revision": "a" * 64, "confirmed": True},
        {"mode": "sandboxed", "revision": "x", "confirmed": False},
        {"mode": "sandboxed", "revision": "a" * 64, "confirmed": "yes"},
    ])
    def test_change_invalid(self, tmp_path, req):
        with pytest.raises(AccessError, match="invalid-request"):
            make_bridge(tmp_path).change(req)

    def test_full_access_requires_confirmation(self, tmp_path):
        with pytest.raises(AccessError, match="confirmation-required"):
            make_bridge(tmp_path).change(
                {"mode": "full-access", "revision": "a" * 64,
                 "confirmed": False})


class TestLocked:
    def test_lock_exclusion(self, tmp_path):
        import fcntl
        b = make_bridge(tmp_path)
        with b.locked():
            fd = os.open(b.state / "lock", os.O_RDONLY)
            try:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                os.close(fd)

    def test_state_dir_created_with_owner_mode(self, tmp_path):
        b = make_bridge(tmp_path)
        with b.locked():
            info = b.state.lstat()
            assert stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o077

    def test_rejects_world_writable_state_dir(self, tmp_path):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o777)
        os.chmod(b.state, 0o777)
        with pytest.raises(AccessError, match="unsafe-host-state"):
            with b.locked():
                pass


class TestPending:
    def test_no_file_returns_none(self, tmp_path):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        assert b.pending() is None

    def test_reads_root_owned_file(self, tmp_path):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        write_private(b.state / "transition.json", {"phase": "held"})
        assert b.pending() == {"phase": "held"}


class TestStoppedNative:
    def test_idle_unit_plus_owned_hold(self, tmp_path, monkeypatch):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        token = "b" * 64
        revision = "c" * 64
        import hashlib
        write_private(
            tmp_path / "home" / ".openclaw/.ods-access-runtime/state.json",
            {"phase": "held",
             "tokenHash": hashlib.sha256(token.encode()).hexdigest(),
             "revision": revision})
        b.home = tmp_path / "home"
        b.owner = type("O", (), {"pw_uid": os.geteuid()})()
        monkeypatch.setattr(
            b, "command",
            lambda *a, **k: "MainPID=0\nActiveState=inactive\nControlGroup=\n")
        result = b.stopped_native(token)
        assert result["stopped"] is True and result["revision"] == revision

    def test_active_unit_rejected(self, tmp_path, monkeypatch):
        b = make_bridge(tmp_path)
        monkeypatch.setattr(
            b, "command",
            lambda *a, **k: "MainPID=123\nActiveState=active\nControlGroup=\n")
        with pytest.raises(AccessError, match="native-idle-unconfirmed"):
            b.stopped_native("b" * 64)

    def test_failed_state_ok_but_wrong_token_rejected(
            self, tmp_path, monkeypatch):
        b = make_bridge(tmp_path)
        b.state.mkdir(mode=0o700)
        write_private(
            tmp_path / "home/.openclaw/.ods-access-runtime/state.json",
            {"phase": "held", "tokenHash": "0" * 64, "revision": "c" * 64})
        b.home = tmp_path / "home"
        b.owner = type("O", (), {"pw_uid": os.geteuid()})()
        monkeypatch.setattr(
            b, "command",
            lambda *a, **k: "MainPID=0\nActiveState=failed\nControlGroup=\n")
        with pytest.raises(AccessError, match="native-lease-unconfirmed"):
            b.stopped_native("b" * 64)


class TestOwnsNativeHold:
    def _snapshot(self, **kw):
        doc = {"available": True, "phase": "held", "active": 0, "pid": 42,
               "revision": "c" * 64}
        doc.update(kw)
        return doc

    def _bridge(self, tmp_path, state=None):
        b = make_bridge(tmp_path)
        b.owner = type("O", (), {"pw_uid": os.geteuid()})()
        b.home = tmp_path / "home"
        if state is not None:
            write_private(
                b.home / ".openclaw/.ods-access-runtime/state.json", state)
        return b

    def test_owned_hold(self, tmp_path):
        import hashlib
        token = "b" * 64
        b = self._bridge(tmp_path, {
            "phase": "held", "revision": "c" * 64,
            "tokenHash": hashlib.sha256(token.encode()).hexdigest()})
        assert b.owns_native_hold(self._snapshot(), token) is True

    @pytest.mark.parametrize("snap_kw", [
        {"available": False}, {"phase": "idle"}, {"phase": "busy"},
        {"active": 1}, {"active": -1}, {"pid": 0}, {"pid": "x"},
        {"revision": "short"},
    ])
    def test_snapshot_must_be_held_idle(self, tmp_path, snap_kw):
        token = "b" * 64
        b = self._bridge(tmp_path, {
            "phase": "held", "revision": "c" * 64,
            "tokenHash": __import__("hashlib").sha256(
                token.encode()).hexdigest()})
        assert b.owns_native_hold(self._snapshot(**snap_kw), token) is False

    @pytest.mark.parametrize("token", ["x", "B" * 64, 5, None])
    def test_bad_token(self, tmp_path, token):
        b = self._bridge(tmp_path)
        assert b.owns_native_hold(self._snapshot(), token) is False

    def test_missing_state_file(self, tmp_path):
        b = self._bridge(tmp_path)  # no state.json written
        assert b.owns_native_hold(self._snapshot(), "b" * 64) is False

    def test_wrong_token_hash(self, tmp_path):
        b = self._bridge(tmp_path, {
            "phase": "held", "revision": "c" * 64, "tokenHash": "0" * 64})
        assert b.owns_native_hold(self._snapshot(), "b" * 64) is False


class TestNativeSnapshot:
    """native_snapshot validates the gateway identity behind one budget."""

    def _bridge(self, tmp_path, snapshot, pid=42):
        b = make_bridge(tmp_path)
        b.native_port, b.native_key = 18789, "k" * 64
        b.native_origin, b._native_identity = None, None
        b.command = lambda *a, **k: str(pid)
        calls = []

        def fake_http(origin, path, key, payload=None, timeout=20):
            calls.append(origin)
            if isinstance(snapshot, Exception):
                raise snapshot
            return snapshot
        b.http = fake_http
        return b, calls

    def _snap(self, **kw):
        doc = {"available": True, "phase": "idle", "active": 0,
               "revision": "c" * 64, "pid": 42}
        doc.update(kw)
        return doc

    def test_healthy_snapshot_returned(self, tmp_path):
        b, calls = self._bridge(tmp_path, self._snap())
        assert b.native_snapshot(timeout=10)["phase"] == "idle"
        assert calls[0].startswith("http://[::1]:")

    def test_pid_mismatch_rejected(self, tmp_path):
        b, _ = self._bridge(tmp_path, self._snap(pid=43), pid=42)
        with pytest.raises(AccessError, match="gateway-process-mismatch"):
            b.native_snapshot(timeout=10)

    @pytest.mark.parametrize("mutation", [
        {"available": False},
        {"phase": "unknown"},
        {"active": -1}, {"active": "0"},
        {"revision": "short"}, {"revision": None},
    ])
    def test_snapshot_invariants(self, tmp_path, mutation):
        b, _ = self._bridge(tmp_path, self._snap(**mutation))
        with pytest.raises(AccessError,
                           match="admission-gate-unavailable|"
                                 "gateway-process-mismatch"):
            b.native_snapshot(timeout=10)

    def test_falls_back_to_ipv4_when_ipv6_unreachable(self, tmp_path):
        b = make_bridge(tmp_path)
        b.native_port, b.native_key = 18789, "k" * 64
        b.native_origin, b._native_identity = None, None
        b.command = lambda *a, **k: "42"
        calls = []

        def fake_http(origin, path, key, payload=None, timeout=20):
            calls.append(origin)
            if "[::1]" in origin:
                raise AccessError("runtime-unavailable-or-busy")
            return self._snap()
        b.http = fake_http
        assert b.native_snapshot(timeout=10)["pid"] == 42
        assert calls == ["http://[::1]:18789", "http://127.0.0.1:18789"]

    def test_no_fallback_on_http_status_errors(self, tmp_path):
        b = make_bridge(tmp_path)
        b.native_port, b.native_key = 18789, "k" * 64
        b.native_origin, b._native_identity = None, None
        b.command = lambda *a, **k: "42"
        calls = []

        def fake_http(origin, path, key, payload=None, timeout=20):
            calls.append(origin)
            raise AccessError("native-transition-busy", http_status=409)
        b.http = fake_http
        with pytest.raises(AccessError, match="native-transition-busy"):
            b.native_snapshot(timeout=10)
        assert calls == ["http://[::1]:18789"]  # no second candidate tried

    def test_pinned_origin_must_be_candidate(self, tmp_path):
        b, _ = self._bridge(tmp_path, self._snap())
        with pytest.raises(AccessError, match="invalid-service-origin"):
            b.native_snapshot(timeout=10,
                              pinned_origin="http://10.0.0.1:18789")

    def test_pinned_origin_used_exclusively(self, tmp_path):
        b, calls = self._bridge(tmp_path, self._snap())
        b.native_snapshot(timeout=10,
                          pinned_origin="http://127.0.0.1:18789")
        assert calls == ["http://127.0.0.1:18789"]

    def test_remembered_origin_tried_first(self, tmp_path):
        b = make_bridge(tmp_path)
        b.native_port, b.native_key = 18789, "k" * 64
        b.native_origin, b._native_identity = None, None
        b.command = lambda *a, **k: "42"
        calls = []
        b.http = lambda origin, path, key, payload=None, timeout=20: (
            calls.append(origin), self._snap())[1]
        b.native_snapshot(timeout=10)   # populates native_origin via ::1 ok
        calls.clear()
        b.native_snapshot(timeout=10)
        assert calls[0] == b.native_origin  # cached origin tried first
