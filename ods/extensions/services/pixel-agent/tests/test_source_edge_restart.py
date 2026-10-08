"""A source retry may restart only the exact, idle, previously verified Edge.

No Docker daemon is needed here. The opt-in Docker test covers the real image,
volume, stopped-container copy and immutable-ID start in Linux CI.
"""
import copy
import json
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "tests"))
import pixel_access_bridge as bridge
from test_pixel_model_transition import FakeBridge

IDENTITY = "a" * 64
KEY = "b" * 64
REVISION = "c" * 64
HASH = "d" * 64


def container(install):
    return {
        "Id": IDENTITY, "Name": "/ods-pixel-edge", "Image": "sha256:" + HASH,
        "Config": {
            "Image": "ods-pixel-edge:local", "User": "pixel",
            "Cmd": ["python3", "edge_entrypoint.py"], "Entrypoint": None,
            "Env": ["PIXEL_PREVIEW_PROXY_KEY=" + KEY,
                    "PIXEL_OPENWEBUI_KEY=" + HASH, "PIXEL_EDGE_PORT_INTERNAL=9595",
                    "PIXEL_TRANSITION_STATE_DIR=/pixel-transition-state",
                    "PIXEL_INGRESS_SOCKET=/pixel-runtime/pixel-ingress.sock",
                    "PIXEL_PREVIEW_SOCKET=/pixel-preview-runtime/http.sock"],
            "Labels": {"com.docker.compose.service": "pixel-edge",
                       "com.docker.compose.project": "ods",
                       "com.docker.compose.project.working_dir": str(install),
                       "com.docker.compose.project.config_files": ",".join((
                           str(install / "docker-compose.base.yml"),
                           str(install / "extensions/services/pixel-edge/compose.yaml")))},
        },
        "HostConfig": {"ReadonlyRootfs": True, "Privileged": False,
                       "PortBindings": {}, "NetworkMode": "ods_default",
                       "PidMode": "", "IpcMode": "private", "CapDrop": ["ALL"],
                       "CapAdd": None, "SecurityOpt": ["no-new-privileges:true"],
                       "Tmpfs": {"/tmp": "size=1M,noexec,nosuid,nodev,mode=1777"}},
        "Mounts": [{"Destination": "/pixel-runtime", "Type": "bind", "RW": False},
                   {"Destination": "/pixel-preview-runtime", "Type": "bind", "RW": False},
                   {"Destination": "/pixel-transition-state", "Type": "volume", "RW": True,
                    "Name": "ods_pixel-transition-state", "Source": "/volume/data"}],
        "State": {"Status": "exited", "Running": False, "Paused": False,
                  "Restarting": False, "Dead": False, "OOMKilled": False,
                  "ExitCode": 0, "Error": "", "Health": {"Status": "unhealthy"}},
    }


def idle():
    return {"version": 1, "phase": "idle", "revision": REVISION,
            "token_hash": None, "released": None}


@pytest.fixture
def adapter(tmp_path):
    result = bridge.SystemdAccessBridge(str(tmp_path), KEY)
    result.state = tmp_path / "protected"
    result.state.mkdir()
    result.item = container(result.install)
    result.volume = {"Name": "ods_pixel-transition-state", "Driver": "local",
                     "Options": None, "Mountpoint": "/volume/data",
                     "Labels": {"com.docker.compose.project": "ods",
                                "com.docker.compose.volume": "pixel-transition-state"}}
    result.consumers = IDENTITY
    result.commands = []

    def command(args, **_kwargs):
        result.commands.append(args)
        if args[:2] == ["docker", "inspect"]:
            return json.dumps(result.item)
        if args[:3] == ["docker", "volume", "inspect"]:
            return json.dumps(result.volume)
        if args[:2] == ["docker", "ps"]:
            return result.consumers
        if args[:2] == ["docker", "start"]:
            assert args == ["docker", "start", IDENTITY]
            result.item["State"].update(Status="running", Running=True, Health={"Status": "healthy"})
            return IDENTITY
        raise AssertionError(args)

    result.command = command
    result._source_edge_restart_proof = Mock(return_value=HASH)
    result._source_edge_idle_state = Mock(side_effect=lambda _id: idle())
    result.edge = Mock(return_value={"capability": "available", "phase": "idle",
                                    "streams": 0, "revision": REVISION})
    return result


def starts(adapter):
    return [args for args in adapter.commands if args[:2] == ["docker", "start"]]


def test_clean_stop_starts_exact_existing_id_once_and_reads_back_gate(adapter):
    adapter.restart_stopped_source_edge()
    assert starts(adapter) == [["docker", "start", IDENTITY]]
    assert adapter._source_edge_restart_proof.call_count == 2
    assert adapter._source_edge_idle_state.call_count == 2
    adapter.edge.assert_called_once_with()
    assert list(adapter.state.iterdir()) == []


@pytest.mark.parametrize("location,key,value", [
    ("Config", "Image", "foreign:latest"), ("Config", "User", "root"),
    ("Config", "Cmd", ["sh"]), ("Config", "Entrypoint", ["sh"]),
    ("HostConfig", "Privileged", True), ("HostConfig", "ReadonlyRootfs", False),
    ("HostConfig", "NetworkMode", "host"), ("HostConfig", "PidMode", "host"),
    ("HostConfig", "CapAdd", ["SYS_ADMIN"]), ("HostConfig", "CapDrop", []),
    ("HostConfig", "PortBindings", {"9595/tcp": [{"HostPort": "9595"}]}),
    ("HostConfig", "Tmpfs", {}), ("HostConfig", "SecurityOpt", ["seccomp=unconfined"]),
])
def test_modified_container_is_not_reopened(adapter, location, key, value):
    adapter.item[location][key] = value
    with pytest.raises(bridge.AccessError, match="source-edge-ownership-unverified"):
        adapter.restart_stopped_source_edge()
    assert starts(adapter) == []


@pytest.mark.parametrize("key,value", [
    ("service", "foreign"), ("project.working_dir", "/other-owner/ods"),
    ("project.config_files", "/other/compose.yaml"), ("project", "FOREIGN"),
])
def test_foreign_compose_identity_refused(adapter, key, value):
    adapter.item["Config"]["Labels"]["com.docker.compose." + key] = value
    with pytest.raises(bridge.AccessError, match="source-edge-ownership-unverified"):
        adapter.restart_stopped_source_edge()
    assert starts(adapter) == []


@pytest.mark.parametrize("change", ["foreign-key", "duplicate-env", "bad-env", "extra-mount",
                                   "shared-volume", "foreign-volume", "volume-options"])
def test_untrusted_credential_or_gate_volume_refused(adapter, change):
    if change == "foreign-key":
        adapter.item["Config"]["Env"][0] = "PIXEL_PREVIEW_PROXY_KEY=" + "f" * 64
    elif change == "duplicate-env":
        adapter.item["Config"]["Env"].append(adapter.item["Config"]["Env"][0])
    elif change == "bad-env":
        adapter.item["Config"]["Env"] = "malformed"
    elif change == "extra-mount":
        adapter.item["Mounts"].append({"Destination": "/host", "Type": "bind", "RW": True})
    elif change == "shared-volume":
        adapter.consumers += "\n" + "f" * 64
    elif change == "foreign-volume":
        adapter.volume["Labels"]["com.docker.compose.project"] = "other"
    else:
        adapter.volume["Options"] = {"type": "nfs"}
    with pytest.raises(bridge.AccessError, match="source-edge-ownership-unverified"):
        adapter.restart_stopped_source_edge()
    assert starts(adapter) == []


@pytest.mark.parametrize("key,value", [
    ("Status", "created"), ("Status", "paused"), ("Status", "running"),
    ("Running", True), ("Paused", True), ("Restarting", True), ("Dead", True),
    ("OOMKilled", True), ("ExitCode", 1), ("ExitCode", False), ("Error", "private daemon detail"),
])
def test_unclean_or_other_lifecycle_state_is_not_restarted(adapter, key, value):
    adapter.item["State"][key] = value
    with pytest.raises(bridge.AccessError, match="source-edge-clean-stop-required"):
        adapter.restart_stopped_source_edge()
    assert starts(adapter) == []


@pytest.mark.parametrize("change", ["native-proof", "gate-revision", "container-id"])
def test_changed_evidence_between_checks_never_starts(adapter, change):
    if change == "native-proof":
        adapter._source_edge_restart_proof.side_effect = [HASH, "f" * 64]
    elif change == "gate-revision":
        adapter._source_edge_idle_state.side_effect = [idle(), {**idle(), "revision": HASH}]
    else:
        original = adapter._source_edge_container
        calls = []
        def inspect():
            result = original()
            calls.append(1)
            if len(calls) == 2:
                result["Id"] = "f" * 64
            return result
        adapter._source_edge_container = inspect
    with pytest.raises(bridge.AccessError, match="source-edge-restart-state-changed"):
        adapter.restart_stopped_source_edge()
    assert starts(adapter) == []


def test_start_failure_is_not_replayed_and_does_not_disclose_daemon_text(adapter):
    original = adapter.command
    def command(args, **kwargs):
        if args[:2] == ["docker", "start"]:
            adapter.commands.append(args)
            raise bridge.AccessError("private credential " + KEY)
        return original(args, **kwargs)
    adapter.command = command
    with pytest.raises(bridge.AccessError) as result:
        adapter.restart_stopped_source_edge()
    assert str(result.value) == "source-edge-start-failed"
    assert len(starts(adapter)) == 1
    adapter.edge.assert_not_called()


@pytest.mark.parametrize("health", ["unhealthy", "starting", None])
def test_readiness_failure_does_not_repeat_start(adapter, health):
    original = adapter.command
    def command(args, **kwargs):
        result = original(args, **kwargs)
        if args[:2] == ["docker", "start"]:
            adapter.item["State"]["Health"] = {"Status": health}
        return result
    adapter.command = command
    with patch.object(bridge, "remaining", return_value=0):
        with pytest.raises(bridge.AccessError, match="source-edge-readiness-unconfirmed"):
            adapter.restart_stopped_source_edge()
    assert len(starts(adapter)) == 1


def test_name_replacement_after_start_cannot_be_accepted(adapter):
    original = adapter.command
    def command(args, **kwargs):
        result = original(args, **kwargs)
        if args[:2] == ["docker", "start"]:
            adapter.item["Image"] = "sha256:" + "f" * 64
        return result
    adapter.command = command
    with pytest.raises(bridge.AccessError, match="source-edge-restart-state-changed"):
        adapter.restart_stopped_source_edge()
    assert starts(adapter) == [["docker", "start", IDENTITY]]
    adapter.edge.assert_not_called()


@pytest.mark.parametrize("key,value", [("phase", "held"), ("streams", 1), ("revision", HASH),
                                       ("capability", "unavailable")])
def test_authenticated_gate_must_match_stopped_idle_revision(adapter, key, value):
    adapter.edge.return_value[key] = value
    with pytest.raises(bridge.AccessError, match="source-edge-admission-changed"):
        adapter.restart_stopped_source_edge()
    assert len(starts(adapter)) == 1


@pytest.fixture
def source_proof(adapter, monkeypatch):
    class UpgradeError(Exception):
        pass
    plan = {"phase": "staged", "hold": None, "before": {"files": HASH},
            "identity": {"configSha256": HASH}}
    manager = SimpleNamespace(journal=lambda: copy.deepcopy(plan), state=adapter.state,
                              downstream_name=lambda: "downstream", verify_mirror=Mock())
    source = SimpleNamespace(begin_plan=Mock(return_value=manager),
                             inventory=Mock(return_value=copy.deepcopy(plan["before"])),
                             UpgradeError=UpgradeError)
    monkeypatch.setitem(sys.modules, "pixel_source_upgrade", source)
    monkeypatch.setattr(bridge.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bridge.os, "geteuid", lambda: 0, raising=False)
    adapter.owner = SimpleNamespace(pw_uid=1001)
    adapter.pending = Mock(return_value=None)
    adapter.discover = Mock()
    adapter.worker = Mock(return_value={"configured_status": "sandboxed", "config_sha256": HASH})
    proof = {"executed": True, "mode": "sandboxed", "pid": 123}
    adapter.native = Mock(return_value={"available": True, "phase": "idle", "active": 0,
                                       "pid": 123, "proof": proof})
    adapter.gateway_service = SimpleNamespace(pid=Mock(return_value=123))
    adapter.unit_boundary = Mock(return_value="protected unit")
    verified = {"pid": 123, "proof": copy.deepcopy(proof), "config_sha256": HASH,
                "boundary": "protected unit"}
    monkeypatch.setattr(bridge, "private_json", Mock(return_value=verified))
    return SimpleNamespace(plan=plan, manager=manager, source=source, verified=verified)


@pytest.mark.parametrize("mode", ["sandboxed", "full-access"])
def test_fresh_native_proof_preserves_chosen_mode(adapter, source_proof, mode):
    adapter.worker.return_value["configured_status"] = mode
    adapter.native.return_value["proof"]["mode"] = mode
    source_proof.verified["proof"]["mode"] = mode
    result = bridge.SystemdAccessBridge._source_edge_restart_proof(adapter)
    assert bridge.HEX.fullmatch(result)
    source_proof.source.begin_plan.assert_called_once_with(adapter.state, adapter.install,
                                                         adapter.owner, installer=True)
    source_proof.manager.verify_mirror.assert_called_once_with()


@pytest.mark.parametrize("change", ["pending", "held-plan", "wrong-phase", "downstream",
    "changed-source", "mode", "active", "phase", "pid", "proof", "verified-boundary",
    "verified-config", "stale-proof", "missing-proof"])
def test_restart_requires_exact_unheld_source_and_live_native_proof(adapter, source_proof, change):
    if change == "pending":
        adapter.pending.return_value = {"phase": "held"}
    elif change == "held-plan":
        source_proof.plan["hold"] = IDENTITY
    elif change == "wrong-phase":
        source_proof.plan["phase"] = "copied"
    elif change == "downstream":
        (adapter.state / "downstream").touch()
    elif change == "changed-source":
        source_proof.source.inventory.return_value = {"files": "different"}
    elif change == "mode":
        adapter.worker.return_value["configured_status"] = "unknown"
    elif change == "active":
        adapter.native.return_value["active"] = 1
    elif change == "phase":
        adapter.native.return_value["phase"] = "held"
    elif change == "pid":
        adapter.gateway_service.pid.return_value = 456
    elif change == "proof":
        adapter.native.return_value["proof"]["executed"] = False
    elif change == "verified-boundary":
        source_proof.verified["boundary"] = "different"
    elif change == "verified-config":
        source_proof.verified["config_sha256"] = IDENTITY
    elif change == "stale-proof":
        source_proof.verified["proof"]["pid"] = 456
    else:
        bridge.private_json.side_effect = FileNotFoundError("private path")
    with pytest.raises(bridge.AccessError):
        bridge.SystemdAccessBridge._source_edge_restart_proof(adapter)
    assert starts(adapter) == []


@pytest.mark.parametrize("body", [idle(), {**idle(), "phase": "held", "token_hash": HASH},
    {**idle(), "phase": "busy"}, {**idle(), "phase": "interrupted"},
    {**idle(), "version": True}, {**idle(), "released": {}},
    '{"version":1,"version":1}', "not JSON", "[]"])
def test_stopped_gate_schema_is_read_before_any_execution(adapter, body, monkeypatch):
    raw = json.dumps(body) if isinstance(body, dict) else body
    commands = []
    def copy_file(args, **_kwargs):
        commands.append(args)
        assert args[:3] == ["docker", "cp", IDENTITY + ":/pixel-transition-state/transition.json"]
        Path(args[3]).write_text(raw, encoding="utf-8")
        return ""
    adapter.command = copy_file
    monkeypatch.setattr(bridge.os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    monkeypatch.setattr(bridge.os, "O_NONBLOCK", getattr(os, "O_NONBLOCK", 0), raising=False)
    original_fstat = os.fstat
    def root_copy_stat(fd):
        info = original_fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return info  # Preserve real directory identity for secure rmtree.
        fields = list(info)
        fields[0], fields[4] = stat.S_IFREG | 0o600, 0
        return os.stat_result(fields)
    monkeypatch.setattr(bridge.os, "fstat", root_copy_stat)
    if isinstance(body, dict) and type(body.get("version")) is int and body == idle():
        assert bridge.SystemdAccessBridge._source_edge_idle_state(adapter, IDENTITY) == idle()
    else:
        with pytest.raises(bridge.AccessError, match="source-edge-admission-unverified"):
            bridge.SystemdAccessBridge._source_edge_idle_state(adapter, IDENTITY)
    assert len(commands) == 1
    assert not Path(commands[0][3]).exists(), "private inspection copy is removed"


def test_no_gate_file_refuses_instead_of_initializing_new_state(adapter):
    adapter.command = Mock(side_effect=bridge.AccessError("host-command-failed"))
    with pytest.raises(bridge.AccessError, match="source-edge-admission-unverified"):
        bridge.SystemdAccessBridge._source_edge_idle_state(adapter, IDENTITY)
    assert adapter.command.call_count == 1


@pytest.mark.parametrize("field,value", [(0, stat.S_IFREG | 0o644), (0, stat.S_IFDIR | 0o600),
                                       (3, 2), (4, 1000), (6, 2049)])
def test_private_gate_copy_must_be_single_regular_root_owned_file(adapter, monkeypatch, field, value):
    def copy_file(args, **_kwargs):
        Path(args[3]).write_text(json.dumps(idle()), encoding="utf-8")
        return ""
    adapter.command = copy_file
    monkeypatch.setattr(bridge.os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    monkeypatch.setattr(bridge.os, "O_NONBLOCK", getattr(os, "O_NONBLOCK", 0), raising=False)
    original_fstat = os.fstat
    def changed_copy(fd):
        info = original_fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return info
        fields = list(info)
        fields[0], fields[4] = stat.S_IFREG | 0o600, 0
        fields[field] = value
        return os.stat_result(fields)
    monkeypatch.setattr(bridge.os, "fstat", changed_copy)
    with pytest.raises(bridge.AccessError, match="source-edge-admission-unverified"):
        bridge.SystemdAccessBridge._source_edge_idle_state(adapter, IDENTITY)


def test_diagnostics_use_only_fixed_recovery_reasons():
    # This pure function has no platform dependency. Load it separately so its
    # wording/redaction is tested on Windows too, without mocking POSIX custody.
    import ast
    tree = ast.parse((ROOT / "bin/pixel_source_upgrade.py").read_text(encoding="utf-8"))
    function = next(item for item in tree.body if isinstance(item, ast.FunctionDef)
                    and item.name == "_failure_message")
    namespace = {"UpgradeError": type("UpgradeError", (RuntimeError,), {}), "re": bridge.re}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "diagnostics", "exec"), namespace)
    message = namespace["_failure_message"]
    result = message(RuntimeError("source-edge-admission-unverified"))
    assert "held, interrupted or unverified" in result
    assert "before acquiring a new hold" in result
    assert KEY not in message(RuntimeError("source-edge-start-failed " + KEY))


@pytest.mark.parametrize("source,stopped", [(True, True), (True, False), (False, True), (False, False)])
def test_source_entrypoint_alone_repairs_stop_then_takes_normal_durable_hold(tmp_path, monkeypatch, source, stopped):
    adapter = FakeBridge(tmp_path)
    adapter.install, adapter.owner = tmp_path, SimpleNamespace(pw_uid=1001)
    original = adapter.inspect
    def inspect(**kwargs):
        if stopped and not adapter.restart_stopped_source_edge.called:
            raise bridge.AccessError("edge-container-unavailable")
        return original(**kwargs)
    adapter.inspect = inspect
    adapter.restart_stopped_source_edge = Mock()
    plan = {"hold": None, "identity": {"configSha256": "a" * 64}}
    manager = SimpleNamespace(journal=lambda: plan, bind=Mock())
    monkeypatch.setitem(sys.modules, "pixel_source_upgrade", SimpleNamespace(
        begin_plan=Mock(return_value=manager), UpgradeError=RuntimeError))
    monkeypatch.setattr(bridge.platform, "system", lambda: "Linux")
    monkeypatch.setattr(bridge.os, "geteuid", lambda: 0, raising=False)
    monkeypatch.setattr(bridge, "atomic_json", lambda path, value: path.write_text(json.dumps(value)))
    if stopped and not source:
        with pytest.raises(bridge.AccessError, match="edge-container-unavailable"):
            adapter.model_begin()
        assert not (tmp_path / "transition.json").exists()
    else:
        result = adapter.model_begin(installer_source=source)
        assert result["status"] == "held"
        assert adapter.pending()["phase"] == "held"
        assert adapter.calls.index("edge:drain") < adapter.calls.index("native:acquire")
        assert "verify:sandboxed" in adapter.calls
    assert adapter.restart_stopped_source_edge.call_count == int(source and stopped)


def test_existing_transition_uses_existing_recovery_without_restart(tmp_path):
    adapter = FakeBridge(tmp_path)
    (tmp_path / "transition.json").write_text('{"phase":"acquiring"}')
    adapter.resume_source_begin = Mock(return_value={"status": "held"})
    adapter.restart_stopped_source_edge = Mock()
    assert adapter.model_begin(installer_source=True) == {"status": "held"}
    adapter.resume_source_begin.assert_called_once_with()
    adapter.restart_stopped_source_edge.assert_not_called()
