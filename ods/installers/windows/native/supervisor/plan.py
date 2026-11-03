# ============================================================================
# ODS Windows-native supervisor — launch plan (pure)
# ============================================================================
# Part of: installers/windows/native/supervisor/
# Purpose: Validate the supervisor config dict written by the installer and
#          turn it into a LaunchPlan: per service argv, cwd, env additions,
#          secret files to load into env, log path, health URL; plus the
#          restart policy and the run/log paths of the supervisor itself.
#
# Expects: A dict parsed from the installer's supervisor config JSON.
# Provides: build_plan(config) -> LaunchPlan   (raises ConfigError)
#           recent_crashes(times, now, policy) -> tuple
#           restart_allowed(times, now, policy) -> bool
#           SERVICE_ORDER, SECRET_FILES, LOOPBACK_HOST
#
# Modder notes:
#   No I/O here: no filesystem checks, no environment reads. Paths are joined
#   with ntpath for Windows roots ("C:\...") and posixpath for POSIX roots so
#   tests can build real plans on Linux. Every service binds 127.0.0.1 only.
#   Secrets are referenced by file path; their values never appear in argv,
#   in the plan, or in the run record.
# ============================================================================
from __future__ import annotations

import ntpath
import posixpath
from dataclasses import dataclass, field
from pathlib import PureWindowsPath

SCHEMA_VERSION = 1
LOOPBACK_HOST = "127.0.0.1"
MIN_PORT, MAX_PORT = 1024, 65535

# Start order: dashboard-api reports on llama-server, so llama-server first.
SERVICE_ORDER = ("llama-server", "dashboard-api")

# File names under <state_root>\secrets. The installer writes these.
SECRET_FILES = {
    "llama-server": "llama-server-api-key",
    "dashboard-api": "dashboard-api-key",
}

_TOP_KEYS = {"schema_version", "install_root", "state_root", "host", "services", "restart", "logs"}
_SERVICE_KEYS = {
    "llama-server": {"enabled", "port", "model_path", "ctx_size", "exe", "webui", "extra_args", "health_timeout_seconds"},
    "dashboard-api": {"enabled", "port", "chat_port", "python_exe", "app_dir", "env", "health_timeout_seconds"},
}
_LLAMA_MANAGED_ARGS = {"--host", "--port", "--api-key", "--api-key-file", "-m", "--model", "-c", "--ctx-size",
                       "--webui", "--no-webui"}
_DASHBOARD_MANAGED_ENV = {
    "DASHBOARD_API_KEY", "LLAMA_SERVER_API_KEY", "DASHBOARD_API_PORT", "ODS_INSTALL_DIR", "ODS_DATA_DIR",
    "ODS_RUNTIME", "ODS_NATIVE_CHAT_PORT", "LLAMA_SERVER_PORT",
}
_DEFAULT_HEALTH_TIMEOUT = {"llama-server": 600.0, "dashboard-api": 60.0}


class ConfigError(ValueError):
    """The supervisor config is invalid; the message names the offending key."""


@dataclass(frozen=True)
class RestartPolicy:
    delay_seconds: float = 5.0
    max_restarts: int = 3
    window_seconds: float = 300.0


@dataclass(frozen=True)
class ServicePlan:
    name: str
    argv: tuple
    cwd: str
    port: int
    health_url: str
    health_timeout_seconds: float
    log_path: str
    env: dict = field(default_factory=dict)
    # env var name -> absolute path of a file whose stripped content is the value
    secret_env: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LaunchPlan:
    services: tuple
    install_root: str
    state_root: str
    log_dir: str
    run_dir: str
    record_path: str
    stop_file: str
    supervisor_log: str
    restart: RestartPolicy
    log_max_bytes: int
    log_backups: int
    poll_interval_seconds: float = 1.0
    health_interval_seconds: float = 1.0
    stop_timeout_seconds: float = 10.0


# ---------------------------------------------------------------- validation helpers

def _require(condition, message):
    if not condition:
        raise ConfigError(message)


def _path_flavor(root, key):
    _require(isinstance(root, str) and root, f"{key} must be a non-empty string")
    if PureWindowsPath(root).drive and ntpath.isabs(root):
        return ntpath
    _require(posixpath.isabs(root) and "\\" not in root, f"{key} must be an absolute path, got {root!r}")
    return posixpath


def _absolute(value, flavor, key):
    _require(isinstance(value, str) and value, f"{key} must be a non-empty string")
    if flavor is ntpath:
        ok = bool(PureWindowsPath(value).drive) and ntpath.isabs(value)
    else:
        ok = posixpath.isabs(value) and "\\" not in value
    _require(ok, f"{key} must be an absolute path, got {value!r}")
    return flavor.normpath(value)


def _int(value, key, low, high):
    _require(isinstance(value, int) and not isinstance(value, bool), f"{key} must be an integer")
    _require(low <= value <= high, f"{key} must be between {low} and {high}, got {value}")
    return value


def _positive_number(value, key):
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{key} must be a number")
    _require(value > 0, f"{key} must be greater than 0, got {value}")
    return float(value)


def _known_keys(mapping, allowed, key):
    _require(isinstance(mapping, dict), f"{key} must be an object")
    unknown = sorted(set(mapping) - allowed)
    _require(not unknown, f"{key} has unknown keys: {', '.join(unknown)}")


def _health_url(port):
    return f"http://{LOOPBACK_HOST}:{port}/health"


# ---------------------------------------------------------------- services

def _llama_plan(svc, ctx):
    flavor, install_root, log_dir, secrets_dir = ctx["flavor"], ctx["install_root"], ctx["log_dir"], ctx["secrets_dir"]
    port = _int(svc.get("port"), "services.llama-server.port", MIN_PORT, MAX_PORT)
    model = _absolute(svc.get("model_path"), flavor, "services.llama-server.model_path")
    ctx_size = _int(svc.get("ctx_size", 8192), "services.llama-server.ctx_size", 512, 1048576)
    default_exe = flavor.join(install_root, "llama", "llama-server.exe")
    exe = _absolute(svc.get("exe", default_exe), flavor, "services.llama-server.exe")
    extra = svc.get("extra_args", [])
    _require(isinstance(extra, list) and all(isinstance(a, str) for a in extra),
             "services.llama-server.extra_args must be a list of strings")
    managed = sorted({a.split("=", 1)[0] for a in extra} & _LLAMA_MANAGED_ARGS)
    _require(not managed, f"services.llama-server.extra_args may not set {', '.join(managed)}; the supervisor sets them")
    webui = svc.get("webui", False)
    _require(isinstance(webui, bool), "services.llama-server.webui must be true or false")
    key_file = flavor.join(secrets_dir, SECRET_FILES["llama-server"])
    # The native app serves llama-server's chat UI through dashboard-api,
    # which adds the API key; the API stays keyed either way.
    webui_args = () if webui else ("--no-webui",)
    argv = (exe, "--model", model, "--host", LOOPBACK_HOST, "--port", str(port),
            "--ctx-size", str(ctx_size), "--api-key-file", key_file, *webui_args, *extra)
    return ServicePlan(
        name="llama-server", argv=argv, cwd=flavor.dirname(exe), port=port,
        health_url=_health_url(port),
        health_timeout_seconds=_positive_number(
            svc.get("health_timeout_seconds", _DEFAULT_HEALTH_TIMEOUT["llama-server"]),
            "services.llama-server.health_timeout_seconds"),
        log_path=flavor.join(log_dir, "llama-server.log"),
    )


def _dashboard_plans(svc, ctx, llama_port):
    """dashboard-api, plus its chat proxy when llama-server runs and chat_port is set.

    llama_port is None when llama-server is disabled.
    """
    flavor, install_root, state_root = ctx["flavor"], ctx["install_root"], ctx["state_root"]
    port = _int(svc.get("port"), "services.dashboard-api.port", MIN_PORT, MAX_PORT)
    python_exe = _absolute(svc.get("python_exe", flavor.join(install_root, "python", "python.exe")),
                           flavor, "services.dashboard-api.python_exe")
    app_dir = _absolute(svc.get("app_dir", flavor.join(install_root, "dashboard-api")),
                        flavor, "services.dashboard-api.app_dir")
    extra_env = svc.get("env", {})
    _require(isinstance(extra_env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in extra_env.items()),
             "services.dashboard-api.env must map strings to strings")
    managed = sorted(set(extra_env) & _DASHBOARD_MANAGED_ENV)
    _require(not managed, f"services.dashboard-api.env may not set {', '.join(managed)}; the supervisor sets them")
    chat_port = svc.get("chat_port")
    if chat_port is not None:
        chat_port = _int(chat_port, "services.dashboard-api.chat_port", MIN_PORT, MAX_PORT)
    # Chat proxies llama-server's built-in UI; without llama-server it has nothing to serve.
    run_chat = chat_port is not None and llama_port is not None
    env = dict(extra_env)
    env.update({
        "ODS_RUNTIME": "native",
        "DASHBOARD_API_PORT": str(port),
        "ODS_INSTALL_DIR": install_root,
        "ODS_DATA_DIR": flavor.join(state_root, "data"),
        "PYTHONUNBUFFERED": "1",
    })
    if llama_port is not None:
        env["LLAMA_SERVER_PORT"] = str(llama_port)
    if run_chat:
        env["ODS_NATIVE_CHAT_PORT"] = str(chat_port)
    secret_env = {"DASHBOARD_API_KEY": flavor.join(ctx["secrets_dir"], SECRET_FILES["dashboard-api"])}
    if llama_port is not None:
        secret_env["LLAMA_SERVER_API_KEY"] = flavor.join(ctx["secrets_dir"], SECRET_FILES["llama-server"])
    health_timeout = _positive_number(
        svc.get("health_timeout_seconds", _DEFAULT_HEALTH_TIMEOUT["dashboard-api"]),
        "services.dashboard-api.health_timeout_seconds")

    def uvicorn(name, app, svc_port):
        argv = (python_exe, "-m", "uvicorn", app, "--host", LOOPBACK_HOST, "--port", str(svc_port))
        return ServicePlan(
            name=name, argv=argv, cwd=app_dir, port=svc_port, health_url=_health_url(svc_port),
            health_timeout_seconds=health_timeout, log_path=flavor.join(ctx["log_dir"], f"{name}.log"),
            env=dict(env), secret_env=dict(secret_env),
        )

    plans = [uvicorn("dashboard-api", "main:app", port)]
    if run_chat:
        # The chat proxy forwards /health to llama-server, which started first.
        plans.append(uvicorn("chat", "native_chat:app", chat_port))
    return plans


def _restart_policy(raw):
    _known_keys(raw, {"delay_seconds", "max_restarts", "window_seconds"}, "restart")
    default = RestartPolicy()
    return RestartPolicy(
        delay_seconds=_positive_number(raw.get("delay_seconds", default.delay_seconds), "restart.delay_seconds"),
        max_restarts=_int(raw.get("max_restarts", default.max_restarts), "restart.max_restarts", 0, 100),
        window_seconds=_positive_number(raw.get("window_seconds", default.window_seconds), "restart.window_seconds"),
    )


# ---------------------------------------------------------------- public API

def build_plan(config):
    """Validate the installer's config dict and return a LaunchPlan. Raises ConfigError."""
    _known_keys(config, _TOP_KEYS, "config")
    _require(config.get("schema_version") == SCHEMA_VERSION,
             f"config.schema_version must be {SCHEMA_VERSION}, got {config.get('schema_version')!r}")
    host = config.get("host", LOOPBACK_HOST)
    _require(host == LOOPBACK_HOST, f"config.host must be {LOOPBACK_HOST}; services bind loopback only, got {host!r}")
    flavor = _path_flavor(config.get("install_root"), "install_root")
    install_root = _absolute(config["install_root"], flavor, "install_root")
    state_root = _absolute(config.get("state_root"), flavor, "state_root")
    log_dir, run_dir = flavor.join(state_root, "logs"), flavor.join(state_root, "run")
    ctx = {"flavor": flavor, "install_root": install_root, "state_root": state_root,
           "log_dir": log_dir, "secrets_dir": flavor.join(state_root, "secrets")}

    services_raw = config.get("services")
    _known_keys(services_raw, set(SERVICE_ORDER), "services")
    for name, svc in services_raw.items():
        _known_keys(svc, _SERVICE_KEYS[name], f"services.{name}")
        _require(isinstance(svc.get("enabled", True), bool), f"services.{name}.enabled must be true or false")
    enabled = [n for n in SERVICE_ORDER if n in services_raw and services_raw[n].get("enabled", True)]
    _require(enabled, "services must enable at least one service")

    services = []
    llama_port = None
    for name in enabled:
        if name == "llama-server":
            services.append(_llama_plan(services_raw[name], ctx))
            llama_port = services[-1].port
        else:
            services.extend(_dashboard_plans(services_raw[name], ctx, llama_port))
    ports = [s.port for s in services]
    _require(len(ports) == len(set(ports)), f"service ports must be distinct, got {ports}")

    logs_raw = config.get("logs", {})
    _known_keys(logs_raw, {"max_bytes", "backups"}, "logs")
    return LaunchPlan(
        services=tuple(services), install_root=install_root, state_root=state_root,
        log_dir=log_dir, run_dir=run_dir,
        record_path=flavor.join(run_dir, "supervisor.json"),
        stop_file=flavor.join(run_dir, "supervisor.stop"),
        supervisor_log=flavor.join(log_dir, "supervisor.log"),
        restart=_restart_policy(config.get("restart", {})),
        log_max_bytes=_int(logs_raw.get("max_bytes", 10 * 1024 * 1024), "logs.max_bytes", 64 * 1024, 1024 ** 3),
        log_backups=_int(logs_raw.get("backups", 3), "logs.backups", 0, 20),
    )


def recent_crashes(crash_times, now, policy):
    """The crash timestamps (monotonic seconds) that fall inside the restart window."""
    return tuple(t for t in crash_times if now - t < policy.window_seconds)


def restart_allowed(crash_times, now, policy):
    """True while the crashes inside the window do not exceed policy.max_restarts."""
    return len(recent_crashes(crash_times, now, policy)) <= policy.max_restarts
