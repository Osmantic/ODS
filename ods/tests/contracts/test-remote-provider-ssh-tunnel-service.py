#!/usr/bin/env python3
"""Remote-provider SSH tunnel service contracts."""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "bin"))
sys.path.insert(0, str(ROOT / "extensions" / "services" / "remote-provider-ssh-tunnel"))

from remote_provider.ssh_supervisor import SSH_SUPERVISOR_PLAN_SCHEMA  # noqa: E402


BASE_COMPOSE = ROOT / "docker-compose.base.yml"
MANIFEST = ROOT / "extensions" / "services" / "remote-provider-ssh-tunnel" / "manifest.yaml"
DOCKERFILE = ROOT / "extensions" / "services" / "remote-provider-ssh-tunnel" / "Dockerfile"
APP_MAIN = ROOT / "extensions" / "services" / "remote-provider-ssh-tunnel" / "app" / "main.py"
EXPOSURE_POLICY = ROOT / "config" / "network-exposure-policy.json"

PUBLIC_SSH_SECRET_ENV = (
    "REMOTE_LLM_SSH_PRIVATE_KEY",
    "REMOTE_LLM_SSH_KNOWN_HOSTS",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class FakeClock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeProcess:
    _next_pid = 2000

    def __init__(self, argv: list[str]) -> None:
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.argv = argv
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0


class FakeProcessFactory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.processes: list[FakeProcess] = []

    def __call__(self, argv: list[str], **kwargs: object) -> FakeProcess:
        process = FakeProcess(argv)
        self.calls.append({"argv": argv, "kwargs": kwargs, "process": process})
        self.processes.append(process)
        return process


class patched_env:
    def __init__(self, **values: str) -> None:
        self.values = values
        self.previous: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            os.environ[key] = value

    def __exit__(self, *_args: object) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def ssh_route_state() -> dict[str, object]:
    return {
        "schema": "ods.remote-routing-state.v1",
        "enabled": True,
        "mode": "cloud",
        "provider": {
            "capability": "openai-compatible",
            "baseUrl": "http://127.0.0.1:8000/v1",
            "model": "qwen/remote:latest",
            "transport": "ssh",
        },
        "ssh": {
            "host": "gpu.example.test",
            "user": "ods",
            "port": 22,
            "inferenceHost": "127.0.0.1",
            "inferencePort": 8000,
            "controlHost": "127.0.0.1",
            "controlPort": 8091,
        },
        "projection": {
            "publicModel": "ods/current",
            "gateway": "litellm-cloud",
            "egressBaseUrl": "http://remote-provider-egress:8091/v1",
            "consumerRoute": "gateway",
        },
        "status": {"proven": False, "reason": "pending-provider-handshake"},
    }


def _compose_block() -> str:
    compose = read(BASE_COMPOSE)
    assert_true("  remote-provider-ssh-tunnel:" in compose, "base compose must define remote-provider-ssh-tunnel")
    return compose.split("  remote-provider-ssh-tunnel:", 1)[1].split("\n  # ", 1)[0]


def _health_app():
    return importlib.import_module("app.main")


def _payload_with_env(route_path: Path, secret_dir: Path) -> dict[str, object]:
    with patched_env(
        ODS_REMOTE_PROVIDER_ROUTE_PATH=str(route_path),
        ODS_REMOTE_PROVIDER_SECRET_DIR=str(secret_dir),
    ):
        return _health_app().health_payload()


def _ready_supervisor_fixture(root: Path) -> tuple[Path, Path]:
    route_path = root / "routing-state.json"
    secret_dir = root / "secrets"
    secret_dir.mkdir()
    route_path.write_text(json.dumps(ssh_route_state()), encoding="utf-8")
    (secret_dir / "ssh-identity").write_text("unit-test-key\n", encoding="utf-8")
    (secret_dir / "known_hosts").write_text("gpu.example.test ssh-ed25519 AAAATEST\n", encoding="utf-8")
    return route_path, secret_dir


def _new_fake_supervisor(route_path: Path, secret_dir: Path):
    ssh_app = _health_app()
    clock = FakeClock()
    factory = FakeProcessFactory()
    supervisor = ssh_app.SshProcessSupervisor(
        route_path,
        secret_dir,
        process_factory=factory,
        monotonic=clock,
        start_grace=1.0,
        restart_cooldown=5.0,
        stop_timeout=0.01,
    )
    return supervisor, factory, clock


def _walk_service_source() -> Iterator[tuple[Path, str]]:
    for path in (MANIFEST, DOCKERFILE, APP_MAIN, BASE_COMPOSE):
        yield path, read(path)


def test_compose_service_is_internal_only_and_hardened() -> None:
    block = _compose_block()
    assert_true("dockerfile: extensions/services/remote-provider-ssh-tunnel/Dockerfile" in block, "service must use its Dockerfile")
    assert_true("image: ods-remote-provider-ssh-tunnel:local" in block, "service image must be local-only")
    assert_true("expose:" in block, "service must expose only internal ports")
    for port in ('"18090"', '"18091"', '"18092"'):
        assert_true(port in block, f"service must expose internal port {port}")
    assert_true("ports:" not in block, "service must not bind a host port")
    assert_true("cap_drop:" in block and "- ALL" in block, "service must drop capabilities")
    assert_true("read_only: true" in block, "service filesystem must be read-only")
    assert_true("/state:ro" in block, "service must mount ODS state read-only")
    assert_true("ODS_REMOTE_PROVIDER_SECRET_DIR=/state/remote-provider/secrets" in block, "service must use secret custody directory")
    for key in PUBLIC_SSH_SECRET_ENV:
        assert_true(key not in block, f"service must not source public SSH secret env {key}")


def test_manifest_and_network_policy_mark_no_lan_exposure() -> None:
    manifest = read(MANIFEST)
    exposure = json.loads(read(EXPOSURE_POLICY))
    assert_true("id: remote-provider-ssh-tunnel" in manifest, "manifest must declare service id")
    assert_true("external_port_default: 0" in manifest, "manifest must prevent host URL fallback")
    assert_true("category: core" in manifest, "SSH tunnel service should be a core internal service")
    assert_true("compose_file:" not in manifest, "base-stack service manifest must not add an extension overlay")
    entry = exposure["services"]["remote-provider-ssh-tunnel"]
    assert_true(entry["lan_exposure"] == "none", "SSH tunnel service must have no LAN exposure")
    assert_true(entry["auth_required"] is True, "SSH tunnel service must require private SSH custody")


def test_image_contains_ssh_client_and_shared_helpers() -> None:
    dockerfile = read(DOCKERFILE)
    assert_true("openssh-client" in dockerfile, "image must include the SSH client")
    assert_true("COPY bin/remote_provider ./remote_provider" in dockerfile, "image must copy shared transport helpers")
    assert_true("USER odsremote" in dockerfile, "image must run as non-root service user")
    assert_true("EXPOSE 18090 18091 18092" in dockerfile, "image must document internal status and tunnel ports")


def test_health_app_reports_disabled_without_route_and_no_secrets() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        payload = _payload_with_env(root / "missing-routing-state.json", root / "secrets")
    dumped = json.dumps(payload, sort_keys=True)
    assert_true(payload["schema"] == "ods.remote-provider-ssh-tunnel-health.v1", "health schema drifted")
    assert_true(payload["ready"] is False, "missing route must not report ready")
    assert_true(payload["status"] == "disabled", "missing route should be a disabled supervisor")
    assert_true(payload["reason"] == "remote_route_disabled", "missing route reason drifted")
    assert_true(payload["plan"]["schema"] == SSH_SUPERVISOR_PLAN_SCHEMA, "plan schema drifted")
    assert_true("unit-test-key" not in dumped, "identity contents leaked into disabled health payload")
    assert_true("AAAATEST" not in dumped, "known_hosts contents leaked into disabled health payload")


def test_health_app_reports_invalid_state_without_starting() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        route_path = root / "routing-state.json"
        route_path.write_text(json.dumps({"schema": "ods.unknown", "enabled": True}), encoding="utf-8")
        payload = _payload_with_env(route_path, root / "secrets")
    assert_true(payload["ready"] is False, "invalid route state must not report ready")
    assert_true(payload["status"] == "invalid", "invalid route state should report a diagnostic status")
    assert_true(payload["reason"] == "route_state_unavailable", "invalid route state reason drifted")
    assert_true(payload["plan"]["readyToStart"] is False, "invalid route state must not be startable")


def test_health_app_reports_planned_when_route_and_secret_custody_exist() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        route_path = root / "routing-state.json"
        secret_dir = root / "secrets"
        secret_dir.mkdir()
        route_path.write_text(json.dumps(ssh_route_state()), encoding="utf-8")
        (secret_dir / "ssh-identity").write_text("unit-test-key\n", encoding="utf-8")
        (secret_dir / "known_hosts").write_text("gpu.example.test ssh-ed25519 AAAATEST\n", encoding="utf-8")
        payload = _payload_with_env(route_path, secret_dir)
    plan = payload["plan"]
    dumped = json.dumps(payload, sort_keys=True)
    assert_true(payload["status"] == "planned", "ready SSH custody should produce a planned status")
    assert_true(payload["ready"] is False, "service scaffold must not report a live tunnel")
    assert_true(plan["readyToStart"] is True, "ready SSH custody should allow a later process start")
    assert_true(plan["reason"] == "tunnel_process_not_started", "planned status reason drifted")
    assert_true(plan["tunnelBaseUrl"] == "http://remote-provider-ssh-tunnel:18091/v1", "tunnel base URL drifted")
    assert_true(len(plan["tunnels"]) == 2, "SSH route should plan inference and control tunnels")
    assert_true("unit-test-key" not in dumped, "identity contents leaked into planned health payload")
    assert_true("AAAATEST" not in dumped, "known_hosts contents leaked into planned health payload")


def test_supervisor_starts_single_ssh_process_without_shell() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        route_path, secret_dir = _ready_supervisor_fixture(Path(tmp))
        supervisor, factory, clock = _new_fake_supervisor(route_path, secret_dir)
        payload = supervisor.reconcile()
        clock.advance(1.1)
        running = supervisor.reconcile()
    argv = factory.calls[0]["argv"]
    kwargs = factory.calls[0]["kwargs"]
    dumped = json.dumps(running, sort_keys=True)
    assert_true(len(factory.calls) == 1, "supervisor should start one SSH child")
    assert_true(isinstance(argv, list) and argv[0] == "ssh", "supervisor must start ssh directly")
    assert_true(kwargs["shell"] is False, "supervisor must not use a shell")
    assert_true(argv.count("-L") == 2, "supervisor must aggregate inference and control forwards")
    assert_true("0.0.0.0:18091:127.0.0.1:8000" in argv, "inference forward missing")
    assert_true("0.0.0.0:18092:127.0.0.1:8091" in argv, "control forward missing")
    assert_true(payload["status"] == "starting", "new SSH child should start in grace period")
    assert_true(running["status"] == "running", "SSH child should become running after grace")
    assert_true(running["ready"] is True, "running SSH child should report ready")
    assert_true(running["process"]["pid"] == factory.processes[0].pid, "process pid should be reported")
    assert_true("unit-test-key" not in dumped, "identity contents leaked into running health payload")
    assert_true("AAAATEST" not in dumped, "known_hosts contents leaked into running health payload")
    assert_true(all(";" not in item and "\n" not in item for item in argv), "SSH argv must not contain shell separators")


def test_supervisor_stops_process_when_secret_custody_disappears() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        route_path, secret_dir = _ready_supervisor_fixture(root)
        supervisor, factory, _clock = _new_fake_supervisor(route_path, secret_dir)
        supervisor.reconcile()
        (secret_dir / "known_hosts").unlink()
        payload = supervisor.reconcile()
    process = factory.processes[0]
    assert_true(process.terminated is True, "supervisor must terminate old SSH child when custody disappears")
    assert_true(payload["status"] == "blocked", "missing custody should block supervisor")
    assert_true(payload["ready"] is False, "missing custody must not report ready")
    assert_true(payload["process"]["status"] == "stopped", "stopped process status drifted")


def test_supervisor_restarts_process_when_route_argv_changes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        route_path, secret_dir = _ready_supervisor_fixture(root)
        supervisor, factory, _clock = _new_fake_supervisor(route_path, secret_dir)
        supervisor.reconcile()
        changed = ssh_route_state()
        changed["ssh"]["inferencePort"] = 8001
        route_path.write_text(json.dumps(changed), encoding="utf-8")
        payload = supervisor.reconcile()
    assert_true(len(factory.calls) == 2, "route argv change should restart SSH child")
    assert_true(factory.processes[0].terminated is True, "old SSH child should be terminated before restart")
    assert_true("0.0.0.0:18091:127.0.0.1:8001" in factory.processes[1].argv, "new SSH child should use updated forward")
    assert_true(payload["status"] == "starting", "restarted child should re-enter grace period")


def test_supervisor_uses_restart_cooldown_after_child_exit() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        route_path, secret_dir = _ready_supervisor_fixture(Path(tmp))
        supervisor, factory, clock = _new_fake_supervisor(route_path, secret_dir)
        supervisor.reconcile()
        factory.processes[0].returncode = 255
        exited = supervisor.reconcile()
        clock.advance(5.1)
        restarted = supervisor.reconcile()
    assert_true(exited["status"] == "exited", "exited SSH child should be reported")
    assert_true(exited["ready"] is False, "exited SSH child must not report ready")
    assert_true(exited["process"]["exitCode"] == 255, "exit code should be reported")
    assert_true(len(factory.calls) == 2, "supervisor should restart only after cooldown")
    assert_true(restarted["status"] == "starting", "restarted child should enter grace period")


def test_timing_environment_rejects_non_finite_values() -> None:
    ssh_app = _health_app()
    for raw in ("nan", "NaN", "inf", "+inf", "-inf"):
        with patched_env(ODS_TEST_SSH_TIMING=raw):
            value = ssh_app._env_float(
                "ODS_TEST_SSH_TIMING",
                5.0,
                minimum=0.1,
            )
        assert_true(value == 5.0, f"non-finite timing {raw!r} must use the default")

    with patched_env(ODS_TEST_SSH_TIMING="0.25"):
        value = ssh_app._env_float("ODS_TEST_SSH_TIMING", 5.0, minimum=0.1)
    assert_true(value == 0.25, "finite timing override should be preserved")


def test_service_source_avoids_public_secret_names() -> None:
    for path, text in _walk_service_source():
        for key in PUBLIC_SSH_SECRET_ENV:
            assert_true(key not in text, f"{key} must not appear in {path}")
    assert_true("ODS_REMOTE_PROVIDER_SECRET_DIR" in read(APP_MAIN), "app must read only the SSH secret custody directory")
    assert_true("ssh_secret_status" in read(APP_MAIN), "app must use shared support-bundle-safe secret status")
    assert_true("ssh_supervisor_plan" in read(APP_MAIN), "app must use shared supervisor planner")
    assert_true("subprocess.Popen" in read(APP_MAIN), "app must use direct process spawning")
    assert_true("shell=False" in read(APP_MAIN), "app must explicitly avoid shell execution")


def main() -> int:
    tests = [
        test_compose_service_is_internal_only_and_hardened,
        test_manifest_and_network_policy_mark_no_lan_exposure,
        test_image_contains_ssh_client_and_shared_helpers,
        test_health_app_reports_disabled_without_route_and_no_secrets,
        test_health_app_reports_invalid_state_without_starting,
        test_health_app_reports_planned_when_route_and_secret_custody_exist,
        test_supervisor_starts_single_ssh_process_without_shell,
        test_supervisor_stops_process_when_secret_custody_disappears,
        test_supervisor_restarts_process_when_route_argv_changes,
        test_supervisor_uses_restart_cooldown_after_child_exit,
        test_timing_environment_rejects_non_finite_values,
        test_service_source_avoids_public_secret_names,
    ]
    for test in tests:
        test()
        print(f"[PASS] {test.__name__}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except AssertionError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        raise SystemExit(1)
