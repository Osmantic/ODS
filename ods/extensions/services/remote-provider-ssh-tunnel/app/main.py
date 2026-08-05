"""Internal-only remote-provider SSH tunnel supervisor service."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from remote_provider.ssh_supervisor import (
    SSH_SUPERVISOR_PLAN_SCHEMA,
    ssh_secret_status,
    ssh_supervisor_plan,
)


HEALTH_SCHEMA = "ods.remote-provider-ssh-tunnel-health.v1"
PROCESS_SCHEMA = "ods.remote-provider-ssh-tunnel-process.v1"
ROUTE_STATE_SCHEMA = "ods.remote-routing-state.v1"
DEFAULT_ROUTE_PATH = Path("/state/remote-provider/routing-state.json")
DEFAULT_SECRET_DIR = Path("/state/remote-provider/secrets")
DEFAULT_STATUS_PORT = 18090
DEFAULT_RECONCILE_INTERVAL_SECONDS = 5.0
DEFAULT_START_GRACE_SECONDS = 1.0
DEFAULT_RESTART_COOLDOWN_SECONDS = 5.0
DEFAULT_STOP_TIMEOUT_SECONDS = 5.0

LOGGER = logging.getLogger("remote-provider-ssh-tunnel")
ProcessFactory = Callable[..., Any]


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name, str(default)))


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name, str(default))
    try:
        value = float(raw)
    except ValueError:
        LOGGER.warning("invalid %s=%r; using default", name, raw)
        return default
    if value < minimum:
        LOGGER.warning("out-of-range %s=%r; using default", name, raw)
        return default
    return value


def _status_port() -> int:
    raw = os.environ.get("ODS_REMOTE_PROVIDER_SSH_STATUS_PORT", str(DEFAULT_STATUS_PORT))
    try:
        port = int(raw)
    except ValueError:
        LOGGER.warning("invalid ODS_REMOTE_PROVIDER_SSH_STATUS_PORT=%r; using default", raw)
        return DEFAULT_STATUS_PORT
    if port < 1 or port > 65535:
        LOGGER.warning("out-of-range ODS_REMOTE_PROVIDER_SSH_STATUS_PORT=%r; using default", raw)
        return DEFAULT_STATUS_PORT
    return port


def _disabled_route() -> dict[str, Any]:
    return {
        "enabled": False,
        "mode": None,
        "transport": "direct",
        "provider": None,
        "ssh": None,
    }


def _route_from_state_doc(doc: Mapping[str, Any]) -> dict[str, Any]:
    if doc.get("schema") != ROUTE_STATE_SCHEMA:
        raise ValueError("unknown route-state schema")
    enabled = doc.get("enabled") is True
    provider = doc.get("provider")
    if enabled and not isinstance(provider, Mapping):
        raise ValueError("enabled route state is missing provider metadata")
    provider_dict = dict(provider) if isinstance(provider, Mapping) else {}
    ssh = doc.get("ssh")
    return {
        "enabled": enabled,
        "mode": doc.get("mode"),
        "transport": str(provider_dict.get("transport") or "direct") if enabled else "direct",
        "provider": provider_dict if enabled else None,
        "ssh": dict(ssh) if isinstance(ssh, Mapping) else None,
    }


def _load_route(route_path: Path) -> dict[str, Any]:
    try:
        raw = route_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _disabled_route()
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("route state is not valid JSON") from exc
    if not isinstance(doc, Mapping):
        raise ValueError("route state must be a JSON object")
    return _route_from_state_doc(doc)


def _error_plan(
    reason: str,
    *,
    status: str = "invalid",
    secret_dir: Path | None = None,
) -> dict[str, Any]:
    return {
        "schema": SSH_SUPERVISOR_PLAN_SCHEMA,
        "status": status,
        "ready": False,
        "readyToStart": False,
        "reason": reason,
        "tunnelBaseUrl": None,
        "tunnels": [],
        "secrets": ssh_secret_status(secret_dir or _env_path("ODS_REMOTE_PROVIDER_SECRET_DIR", DEFAULT_SECRET_DIR)),
        "missingSecrets": [],
    }


def _supervisor_plan_for_paths(route_path: Path, secret_dir: Path) -> dict[str, Any]:
    try:
        route = _load_route(route_path)
    except (OSError, ValueError) as exc:
        LOGGER.info("remote route state unavailable: %s", exc)
        return _error_plan("route_state_unavailable", secret_dir=secret_dir)
    try:
        return ssh_supervisor_plan(route, secrets=ssh_secret_status(secret_dir))
    except (TypeError, ValueError) as exc:
        LOGGER.info("remote SSH supervisor plan unavailable: %s", exc)
        return _error_plan("ssh_plan_unavailable", secret_dir=secret_dir)


def _supervisor_plan() -> dict[str, Any]:
    return _supervisor_plan_for_paths(
        _env_path("ODS_REMOTE_PROVIDER_ROUTE_PATH", DEFAULT_ROUTE_PATH),
        _env_path("ODS_REMOTE_PROVIDER_SECRET_DIR", DEFAULT_SECRET_DIR),
    )


def _argv_without_single_forward(argv: Sequence[str]) -> tuple[list[str], str]:
    if len(argv) < 2:
        raise ValueError("SSH tunnel argv is incomplete")
    destination = argv[-1]
    base: list[str] = []
    idx = 0
    while idx < len(argv) - 1:
        item = argv[idx]
        if item == "-L":
            idx += 2
            continue
        base.append(item)
        idx += 1
    return base, destination


def _forward_from_tunnel(tunnel: Mapping[str, Any]) -> str:
    argv = tunnel.get("argv")
    if not isinstance(argv, list):
        raise ValueError("SSH tunnel argv is missing")
    try:
        forward_index = argv.index("-L")
    except ValueError as exc:
        raise ValueError("SSH tunnel argv is missing a local forward") from exc
    try:
        forward = argv[forward_index + 1]
    except IndexError as exc:
        raise ValueError("SSH tunnel argv local forward is incomplete") from exc
    if not isinstance(forward, str) or not forward:
        raise ValueError("SSH tunnel argv local forward is invalid")
    return forward


def _combined_ssh_argv(plan: Mapping[str, Any]) -> tuple[str, ...] | None:
    if plan.get("readyToStart") is not True:
        return None
    raw_tunnels = plan.get("tunnels")
    if not isinstance(raw_tunnels, list) or not raw_tunnels:
        raise ValueError("SSH supervisor plan has no tunnels")
    first = raw_tunnels[0]
    if not isinstance(first, Mapping):
        raise ValueError("SSH supervisor plan has an invalid tunnel")
    first_argv = first.get("argv")
    if not isinstance(first_argv, list) or not all(isinstance(item, str) for item in first_argv):
        raise ValueError("SSH supervisor plan has invalid argv")
    base, destination = _argv_without_single_forward(first_argv)
    forwards: list[str] = []
    for tunnel in raw_tunnels:
        if not isinstance(tunnel, Mapping):
            raise ValueError("SSH supervisor plan has an invalid tunnel")
        forwards.extend(("-L", _forward_from_tunnel(tunnel)))
    return tuple(base + forwards + [destination])


def _process_status(
    status: str,
    *,
    pid: int | None = None,
    exit_code: int | None = None,
    uptime_seconds: float | None = None,
    restart_after_seconds: float | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": PROCESS_SCHEMA,
        "status": status,
        "pid": pid,
        "exitCode": exit_code,
        "uptimeSeconds": round(uptime_seconds, 3) if uptime_seconds is not None else None,
        "restartAfterSeconds": (
            round(restart_after_seconds, 3)
            if restart_after_seconds is not None
            else None
        ),
        "errorType": error_type,
    }


def _health_from_plan(
    plan: Mapping[str, Any],
    process: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema": HEALTH_SCHEMA,
        "ready": bool(plan.get("ready")),
        "status": plan.get("status"),
        "reason": plan.get("reason"),
        "plan": dict(plan),
        "process": dict(process or _process_status("unmanaged")),
    }


class SshProcessSupervisor:
    def __init__(
        self,
        route_path: Path,
        secret_dir: Path,
        *,
        process_factory: ProcessFactory = subprocess.Popen,
        monotonic: Callable[[], float] = time.monotonic,
        reconcile_interval: float = DEFAULT_RECONCILE_INTERVAL_SECONDS,
        start_grace: float = DEFAULT_START_GRACE_SECONDS,
        restart_cooldown: float = DEFAULT_RESTART_COOLDOWN_SECONDS,
        stop_timeout: float = DEFAULT_STOP_TIMEOUT_SECONDS,
    ) -> None:
        self.route_path = route_path
        self.secret_dir = secret_dir
        self.process_factory = process_factory
        self.monotonic = monotonic
        self.reconcile_interval = reconcile_interval
        self.start_grace = start_grace
        self.restart_cooldown = restart_cooldown
        self.stop_timeout = stop_timeout
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any | None = None
        self._process_signature: str | None = None
        self._started_at: float | None = None
        self._last_exit_code: int | None = None
        self._last_exit_at: float | None = None
        self._last_payload = _health_from_plan(
            _error_plan("not_reconciled", status="starting", secret_dir=secret_dir),
            _process_status("stopped"),
        )

    def start(self) -> None:
        self.reconcile()
        self._thread = threading.Thread(target=self._run, name="ssh-supervisor", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=max(self.reconcile_interval, 1.0) + 1.0)
        with self._lock:
            self._stop_process_locked()

    def _run(self) -> None:
        while not self._stop_event.wait(self.reconcile_interval):
            self.reconcile()

    def health_payload(self, *, reconcile: bool = False) -> dict[str, Any]:
        if reconcile:
            return self.reconcile()
        with self._lock:
            return json.loads(json.dumps(self._last_payload))

    def reconcile(self) -> dict[str, Any]:
        plan = _supervisor_plan_for_paths(self.route_path, self.secret_dir)
        now = self.monotonic()
        with self._lock:
            self._notice_exited_locked(now)
            try:
                argv = _combined_ssh_argv(plan)
            except ValueError as exc:
                LOGGER.warning("ssh plan argv invalid: %s", exc)
                self._stop_process_locked()
                self._last_payload = _health_from_plan(
                    _error_plan("ssh_plan_unavailable", secret_dir=self.secret_dir),
                    _process_status("stopped"),
                )
                return json.loads(json.dumps(self._last_payload))
            if argv is None:
                self._stop_process_locked()
                self._last_payload = _health_from_plan(plan, _process_status("stopped"))
                return json.loads(json.dumps(self._last_payload))

            signature = "\0".join(argv)
            if self._process is not None and self._process_signature != signature:
                self._stop_process_locked()
            if self._process is None:
                cooldown_remaining = self._restart_cooldown_remaining(now)
                if cooldown_remaining > 0:
                    cooled_plan = self._process_plan(
                        plan,
                        status="exited",
                        ready=False,
                        reason="ssh_process_exited",
                    )
                    self._last_payload = _health_from_plan(
                        cooled_plan,
                        _process_status(
                            "exited",
                            exit_code=self._last_exit_code,
                            restart_after_seconds=cooldown_remaining,
                        ),
                    )
                    return json.loads(json.dumps(self._last_payload))
                started = self._start_process_locked(argv, signature, now)
                if started is not None:
                    self._last_payload = started
                    return json.loads(json.dumps(self._last_payload))

            self._last_payload = self._running_payload_locked(plan, now)
            return json.loads(json.dumps(self._last_payload))

    def _notice_exited_locked(self, now: float) -> None:
        if self._process is None:
            return
        exit_code = self._process.poll()
        if exit_code is None:
            return
        self._last_exit_code = int(exit_code)
        self._last_exit_at = now
        self._process = None
        self._process_signature = None
        self._started_at = None

    def _restart_cooldown_remaining(self, now: float) -> float:
        if self._last_exit_at is None:
            return 0.0
        elapsed = max(0.0, now - self._last_exit_at)
        return max(0.0, self.restart_cooldown - elapsed)

    def _start_process_locked(
        self,
        argv: tuple[str, ...],
        signature: str,
        now: float,
    ) -> dict[str, Any] | None:
        try:
            process = self.process_factory(
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=True,
            )
        except OSError as exc:
            LOGGER.warning("failed to start SSH tunnel process: %s", exc.__class__.__name__)
            plan = _error_plan("ssh_process_start_failed", status="error", secret_dir=self.secret_dir)
            return _health_from_plan(
                plan,
                _process_status("error", error_type=exc.__class__.__name__),
            )
        self._process = process
        self._process_signature = signature
        self._started_at = now
        self._last_exit_code = None
        self._last_exit_at = None
        return None

    def _running_payload_locked(self, plan: Mapping[str, Any], now: float) -> dict[str, Any]:
        uptime = 0.0 if self._started_at is None else max(0.0, now - self._started_at)
        starting = uptime < self.start_grace
        status = "starting" if starting else "running"
        return _health_from_plan(
            self._process_plan(
                plan,
                status=status,
                ready=not starting,
                reason=f"ssh_process_{status}",
            ),
            _process_status(
                status,
                pid=getattr(self._process, "pid", None),
                uptime_seconds=uptime,
            ),
        )

    def _process_plan(
        self,
        plan: Mapping[str, Any],
        *,
        status: str,
        ready: bool,
        reason: str,
    ) -> dict[str, Any]:
        updated = dict(plan)
        updated.update({"status": status, "ready": ready, "reason": reason})
        return updated

    def _stop_process_locked(self) -> None:
        process = self._process
        self._process = None
        self._process_signature = None
        self._started_at = None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=self.stop_timeout)
        except subprocess.TimeoutExpired:
            LOGGER.warning("SSH tunnel process did not terminate in time; killing")
            process.kill()
            try:
                process.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                LOGGER.warning("SSH tunnel process did not exit after kill")


def health_payload(supervisor: SshProcessSupervisor | None = None) -> dict[str, Any]:
    if supervisor is not None:
        return supervisor.health_payload()
    return _health_from_plan(_supervisor_plan())


class HealthHandler(BaseHTTPRequestHandler):
    server_version = "ODSRemoteProviderSSHTunnel/1"
    sys_version = ""

    def do_GET(self) -> None:
        if self.path != "/health":
            self._write_json({"error": "not_found"}, status=404)
            return
        supervisor = getattr(self.server, "supervisor", None)
        self._write_json(health_payload(supervisor))

    def log_message(self, fmt: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), fmt % args)

    def _write_json(self, payload: Mapping[str, Any], *, status: int = 200) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def main() -> int:
    logging.basicConfig(level=os.environ.get("ODS_LOG_LEVEL", "INFO"))
    supervisor = SshProcessSupervisor(
        _env_path("ODS_REMOTE_PROVIDER_ROUTE_PATH", DEFAULT_ROUTE_PATH),
        _env_path("ODS_REMOTE_PROVIDER_SECRET_DIR", DEFAULT_SECRET_DIR),
        reconcile_interval=_env_float(
            "ODS_REMOTE_PROVIDER_SSH_RECONCILE_INTERVAL",
            DEFAULT_RECONCILE_INTERVAL_SECONDS,
            minimum=0.5,
        ),
        start_grace=_env_float(
            "ODS_REMOTE_PROVIDER_SSH_START_GRACE",
            DEFAULT_START_GRACE_SECONDS,
        ),
        restart_cooldown=_env_float(
            "ODS_REMOTE_PROVIDER_SSH_RESTART_COOLDOWN",
            DEFAULT_RESTART_COOLDOWN_SECONDS,
        ),
        stop_timeout=_env_float(
            "ODS_REMOTE_PROVIDER_SSH_STOP_TIMEOUT",
            DEFAULT_STOP_TIMEOUT_SECONDS,
        ),
    )
    server = ThreadingHTTPServer(("0.0.0.0", _status_port()), HealthHandler)
    server.supervisor = supervisor
    LOGGER.info("remote-provider SSH tunnel supervisor listening on %s", server.server_address)
    try:
        supervisor.start()
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("remote-provider SSH tunnel supervisor stopping")
    finally:
        supervisor.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
