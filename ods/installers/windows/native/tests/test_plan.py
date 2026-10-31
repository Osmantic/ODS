"""Pure launch-plan tests: config validation and plan contents."""
import copy

import pytest

from supervisor.plan import (
    ConfigError, RestartPolicy, build_plan, recent_crashes, restart_allowed,
)

WIN_CONFIG = {
    "schema_version": 1,
    "install_root": r"C:\Users\u\AppData\Local\Programs\ODS",
    "state_root": r"C:\Users\u\AppData\Local\ODS",
    "services": {
        "llama-server": {"port": 8080, "model_path": r"C:\Users\u\AppData\Local\ODS\models\m.gguf", "ctx_size": 4096},
        "dashboard-api": {"port": 3002, "env": {"LLM_API_URL": "http://127.0.0.1:8080"}},
    },
}


def config(**overrides):
    cfg = copy.deepcopy(WIN_CONFIG)
    cfg.update(overrides)
    return cfg


def test_windows_config_builds_ordered_loopback_plan():
    plan = build_plan(config())
    llama, dash = plan.services
    assert [s.name for s in plan.services] == ["llama-server", "dashboard-api"]
    assert llama.argv == (
        r"C:\Users\u\AppData\Local\Programs\ODS\llama\llama-server.exe",
        "--model", r"C:\Users\u\AppData\Local\ODS\models\m.gguf",
        "--host", "127.0.0.1", "--port", "8080", "--ctx-size", "4096",
        "--api-key-file", r"C:\Users\u\AppData\Local\ODS\secrets\llama-server-api-key", "--no-webui",
    )
    assert llama.cwd == r"C:\Users\u\AppData\Local\Programs\ODS\llama"
    assert llama.health_url == "http://127.0.0.1:8080/health"
    assert llama.log_path == r"C:\Users\u\AppData\Local\ODS\logs\llama-server.log"
    assert dash.argv == (r"C:\Users\u\AppData\Local\Programs\ODS\python\python.exe", "-m", "uvicorn", "main:app",
                         "--host", "127.0.0.1", "--port", "3002")
    assert dash.cwd == r"C:\Users\u\AppData\Local\Programs\ODS\dashboard-api"
    assert dash.env["LLM_API_URL"] == "http://127.0.0.1:8080"
    assert dash.env["DASHBOARD_API_PORT"] == "3002"
    assert dash.env["ODS_DATA_DIR"] == r"C:\Users\u\AppData\Local\ODS\data"
    assert dash.secret_env == {
        "DASHBOARD_API_KEY": r"C:\Users\u\AppData\Local\ODS\secrets\dashboard-api-key",
        "LLAMA_SERVER_API_KEY": r"C:\Users\u\AppData\Local\ODS\secrets\llama-server-api-key",
    }
    assert plan.record_path == r"C:\Users\u\AppData\Local\ODS\run\supervisor.json"
    assert plan.stop_file == r"C:\Users\u\AppData\Local\ODS\run\supervisor.stop"
    assert plan.restart == RestartPolicy()


def test_posix_roots_build_a_plan_for_linux_tests():
    plan = build_plan(config(install_root="/opt/ods", state_root="/var/ods", services={
        "llama-server": {"port": 8080, "model_path": "/var/ods/models/m.gguf"}}))
    assert plan.services[0].argv[0] == "/opt/ods/llama/llama-server.exe"
    assert plan.record_path == "/var/ods/run/supervisor.json"


def test_disabled_llama_drops_service_and_its_key():
    cfg = config()
    cfg["services"]["llama-server"]["enabled"] = False
    plan = build_plan(cfg)
    assert [s.name for s in plan.services] == ["dashboard-api"]
    assert "LLAMA_SERVER_API_KEY" not in plan.services[0].secret_env


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "::1", "192.168.1.5"])
def test_rejects_non_loopback_host(host):
    with pytest.raises(ConfigError, match="127.0.0.1"):
        build_plan(config(host=host))


@pytest.mark.parametrize("port", [80, 0, 65536, "8080", True, None, 8080.0])
def test_rejects_bad_port(port):
    cfg = config()
    cfg["services"]["llama-server"]["port"] = port
    with pytest.raises(ConfigError, match="services.llama-server.port"):
        build_plan(cfg)


def test_rejects_duplicate_ports():
    cfg = config()
    cfg["services"]["dashboard-api"]["port"] = 8080
    with pytest.raises(ConfigError, match="distinct"):
        build_plan(cfg)


@pytest.mark.parametrize("key,value", [
    ("install_root", r"Programs\ODS"),
    ("install_root", "C:Programs"),
    ("install_root", ""),
    ("state_root", r"ODS\state"),
    ("state_root", "/var/ods"),  # mixed with a Windows install_root
])
def test_rejects_relative_or_mixed_roots(key, value):
    with pytest.raises(ConfigError, match=key):
        build_plan(config(**{key: value}))


@pytest.mark.parametrize("field,value", [
    ("model_path", r"models\m.gguf"),
    ("exe", "llama-server.exe"),
])
def test_rejects_relative_service_paths(field, value):
    cfg = config()
    cfg["services"]["llama-server"][field] = value
    with pytest.raises(ConfigError, match=f"services.llama-server.{field}"):
        build_plan(cfg)


@pytest.mark.parametrize("mutate,match", [
    (lambda c: c.update(schema_version=2), "schema_version"),
    (lambda c: c.update(typo=1), "unknown keys: typo"),
    (lambda c: c["services"].update(ollama={}), "unknown keys: ollama"),
    (lambda c: c["services"]["dashboard-api"].update(prot=1), "unknown keys: prot"),
    (lambda c: c["services"]["llama-server"].update(extra_args=["--host", "0.0.0.0"]), "--host"),
    (lambda c: c["services"]["llama-server"].update(extra_args=["--api-key=x"]), "--api-key"),
    (lambda c: c["services"]["llama-server"].update(extra_args="--metrics"), "extra_args"),
    (lambda c: c["services"]["dashboard-api"].update(env={"DASHBOARD_API_KEY": "x"}), "DASHBOARD_API_KEY"),
    (lambda c: c["services"]["dashboard-api"].update(enabled="yes"), "enabled"),
    (lambda c: [s.update(enabled=False) for s in c["services"].values()], "at least one"),
    (lambda c: c.update(restart={"max_restarts": -1}), "restart.max_restarts"),
    (lambda c: c.update(restart={"delay_seconds": 0}), "restart.delay_seconds"),
    (lambda c: c.update(logs={"max_bytes": 10}), "logs.max_bytes"),
    (lambda c: c.pop("services"), "services must be an object"),
])
def test_rejects_invalid_config(mutate, match):
    cfg = config()
    mutate(cfg)
    with pytest.raises(ConfigError, match=match):
        build_plan(cfg)


def test_restart_window_counts_only_recent_crashes():
    policy = RestartPolicy(delay_seconds=1, max_restarts=2, window_seconds=100)
    assert recent_crashes((0, 50, 150), 160, policy) == (150,)
    assert restart_allowed((10, 20), 30, policy)
    assert not restart_allowed((10, 20, 30), 30, policy)
    assert restart_allowed((10, 20, 30), 115, policy)
