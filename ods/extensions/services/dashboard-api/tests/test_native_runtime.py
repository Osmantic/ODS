"""ODS_RUNTIME=native: services, llama-server key, status/readiness, metrics, data dir."""

import asyncio
import ctypes
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import helpers
import native_runtime
import windows_metrics
from models import BootstrapStatus, ServiceStatus

API_DIR = Path(__file__).resolve().parents[1]


# --- runtime selection ---------------------------------------------------------

def test_runtime_defaults_to_docker_and_rejects_unknown_values():
    assert native_runtime.parse_runtime(None, platform="linux") == "docker"
    assert native_runtime.parse_runtime(None, platform="win32") == "native"
    assert native_runtime.parse_runtime("docker", platform="win32") == "docker"
    assert native_runtime.parse_runtime(" Native ") == "native"
    with pytest.raises(ValueError):
        native_runtime.parse_runtime("wsl")


def test_enabled_services_default_and_validation():
    assert native_runtime.parse_enabled_services("") == {"llama-server", "dashboard-api"}
    assert native_runtime.parse_enabled_services("llama-server, dashboard-api,whisper") == {
        "llama-server", "dashboard-api", "whisper"}
    with pytest.raises(ValueError):
        native_runtime.parse_enabled_services("llama-server,../etc")


def test_native_ports():
    assert native_runtime.native_port_override("llama-server", {}) == 8080
    assert native_runtime.native_port_override("llama-server", {"LLAMA_SERVER_PORT": "18080"}) == 18080
    assert native_runtime.native_port_override("dashboard-api", {"LLAMA_SERVER_PORT": "1"}) is None
    with pytest.raises(ValueError):
        native_runtime.native_port_override("llama-server", {"LLAMA_SERVER_PORT": "70000"})
    assert native_runtime.parse_chat_port("3010") == 3010
    assert native_runtime.parse_chat_port(None) is None


def test_docker_mode_offers_every_feature():
    assert native_runtime.runtime_summary() == {"kind": "docker", "unavailable": []}


@pytest.mark.parametrize("method,path,feature", [
    ("POST", "/api/extensions/n8n/install", "extensions"),
    ("GET", "/api/extensions/catalog", None),
    ("GET", "/api/talk/status", "talk"),
    ("GET", "/api/setup/network-status", "wifi"),
    ("GET", "/api/models", None),
    ("POST", "/api/chat", None),
])
def test_unavailable_feature_table(method, path, feature):
    assert native_runtime.unavailable_feature(method, path) == feature


# --- llama-server key ------------------------------------------------------------

def test_key_file_is_read_and_preferred(tmp_path):
    key_file = tmp_path / "llama-server-api-key"
    key_file.write_text("file-key\r\n", encoding="utf-8")
    env = {"LLAMA_SERVER_API_KEY_FILE": str(key_file), "LLAMA_SERVER_API_KEY": "env-key"}
    assert native_runtime.read_llama_server_api_key(env) == "file-key"
    assert native_runtime.read_llama_server_api_key({"LLAMA_SERVER_API_KEY": "env-key"}) == "env-key"
    assert native_runtime.read_llama_server_api_key({}) == ""


def test_bad_key_configuration_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        native_runtime.read_llama_server_api_key({"LLAMA_SERVER_API_KEY_FILE": str(tmp_path / "missing")})
    (tmp_path / "empty").write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError):
        native_runtime.read_llama_server_api_key({"LLAMA_SERVER_API_KEY_FILE": str(tmp_path / "empty")})
    with pytest.raises(ValueError):
        native_runtime.read_llama_server_api_key({"LLAMA_SERVER_API_KEY": "two words"})


@pytest.mark.parametrize("url,expected", [
    ("http://127.0.0.1:8080/props", True),
    ("http://127.0.0.1:8080/v1/chat/completions", True),
    ("http://127.0.0.1:8081/props", False),
    ("https://127.0.0.1:8080/props", False),
    ("http://litellm:4000/v1/models", False),
    ("http://127.0.0.1.evil.example:8080/", False),
])
def test_key_is_bound_to_the_llama_origin(url, expected):
    headers = native_runtime.bearer_for_origin(url, "127.0.0.1", 8080, "k")
    assert headers == ({"Authorization": "Bearer k"} if expected else {})


def test_llama_requests_carry_the_key_only_in_native_mode(monkeypatch):
    monkeypatch.setitem(config.SERVICES, "llama-server", {"host": "127.0.0.1", "port": 8080})
    monkeypatch.setattr(helpers, "llama_server_api_key", lambda: "k")
    monkeypatch.setattr(helpers, "NATIVE", False)
    assert helpers.llama_auth_headers("http://127.0.0.1:8080/props") == {}
    monkeypatch.setattr(helpers, "NATIVE", True)
    assert helpers.llama_auth_headers("http://127.0.0.1:8080/props") == {"Authorization": "Bearer k"}


def test_native_status_reads_the_model_with_the_key(monkeypatch):
    monkeypatch.setitem(config.SERVICES, "llama-server", {"host": "127.0.0.1", "port": 8080})
    monkeypatch.setattr(helpers, "llama_server_api_key", lambda: "k")
    monkeypatch.setattr(helpers, "NATIVE", True)
    seen = []

    class Client:
        async def get(self, url, headers=None, **_):
            seen.append(headers)
            if headers != {"Authorization": "Bearer k"}:
                return SimpleNamespace(json=lambda: {"error": "Invalid API Key"})
            if "/props" in url:
                return SimpleNamespace(json=lambda: {"default_generation_settings": {"n_ctx": 8192}})
            return SimpleNamespace(json=lambda: {"data": [{"id": "qwen.gguf"}]})

    async def client():
        return Client()
    monkeypatch.setattr(helpers, "_get_httpx_client", client)
    assert asyncio.run(helpers.get_loaded_model()) == "qwen.gguf"
    assert asyncio.run(helpers.get_llama_context_size("qwen.gguf")) == 8192
    assert seen == [{"Authorization": "Bearer k"}] * 2


def test_native_setup_chat_goes_to_llama_with_its_key(monkeypatch):
    import setup_chat_route
    monkeypatch.setattr(setup_chat_route, "NATIVE", True)
    monkeypatch.setattr(setup_chat_route, "llama_server_api_key", lambda: "k")
    monkeypatch.setattr(setup_chat_route, "read_live_env_values", lambda keys: {
        **{key: "" for key in keys}, "LLM_API_URL": "http://litellm:4000", "GGUF_FILE": "qwen.gguf",
        "AMD_INFERENCE_LOCATION": "host", "LITELLM_KEY": "gateway-secret"})
    url, model, headers = setup_chat_route.resolve_chat_route("http://127.0.0.1:8080")
    assert url == "http://127.0.0.1:8080/v1/chat/completions"
    assert model == "qwen.gguf"
    assert headers["Authorization"] == "Bearer k"


# --- manifests, status and readiness --------------------------------------------------

def _manifest(root, service_id, body):
    (root / service_id).mkdir()
    (root / service_id / "manifest.yaml").write_text(body, encoding="utf-8")


def test_native_manifests_run_on_loopback_at_published_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_PORT", "13002")
    monkeypatch.setenv("LLAMA_SERVER_PORT", "18080")
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    _manifest(tmp_path, "llama-server", "schema_version: ods.services.v1\nservice:\n  id: llama-server\n"
              "  host_env: OLLAMA_HOST\n  default_host: llama-server\n  port: 8080\n"
              "  external_port_env: OLLAMA_PORT\n  external_port_default: 11434\n  type: docker\n"
              "  gpu_backends: [nvidia]\n")
    _manifest(tmp_path, "dashboard-api", "schema_version: ods.services.v1\nservice:\n  id: dashboard-api\n"
              "  default_host: dashboard-api\n  port: 3002\n  external_port_env: DASHBOARD_API_PORT\n"
              "  external_port_default: 3002\n  type: docker\n  compose_file: compose.yaml\n"
              "  gpu_backends: [amd, nvidia]\n")
    _manifest(tmp_path, "open-webui", "schema_version: ods.services.v1\nservice:\n  id: open-webui\n  port: 8080\n")
    services, _, errors = config.load_extension_manifests(
        tmp_path, "cpu", only_service_ids=frozenset({"llama-server", "dashboard-api"}), native=True)
    assert errors == []
    assert set(services) == {"llama-server", "dashboard-api"}
    assert (services["llama-server"]["host"], services["llama-server"]["port"]) == ("127.0.0.1", 18080)
    assert (services["dashboard-api"]["host"], services["dashboard-api"]["port"]) == ("127.0.0.1", 13002)
    docker, _, _ = config.load_extension_manifests(tmp_path, "nvidia")
    assert docker["llama-server"]["host"] == "llama-server"
    assert "dashboard-api" not in docker  # its compose.yaml is not installed


def test_native_status_probes_only_enabled_services_without_the_host_agent(monkeypatch):
    services = {"llama-server": {"name": "llama", "port": 8080}, "dashboard-api": {"name": "api", "port": 3002}}
    monkeypatch.setattr(helpers, "SERVICES", services)
    monkeypatch.setattr(helpers, "NATIVE", True)

    async def probe(service_id, cfg, **_):
        return ServiceStatus(id=service_id, name=cfg["name"], port=cfg["port"], external_port=cfg["port"],
                             status="down" if service_id == "llama-server" else "healthy")

    async def no_agent(*_args, **_kwargs):
        raise AssertionError("native status must not call the host agent")
    monkeypatch.setattr(helpers, "check_service_health", probe)
    monkeypatch.setattr(helpers, "request_agent_json", no_agent)
    monkeypatch.setattr(helpers, "load_extension_manifests", no_agent)
    statuses = asyncio.run(helpers.get_all_services())
    assert {(s.id, s.status) for s in statuses} == {("llama-server", "down"), ("dashboard-api", "healthy")}


def test_native_readiness_reports_only_enabled_services():
    import main
    status = [ServiceStatus(id="llama-server", name="llama", port=8080, external_port=8080, status="healthy")]
    payload = main._build_readiness_payload(
        enabled_services=frozenset({"llama-server", "dashboard-api"}),
        service_statuses=status, loaded_model="qwen.gguf", context_size=8192,
        bootstrap_info=BootstrapStatus(active=False), host_agent={"available": False},
        stt_model_cached=None, stt_model_name="whisper",
    )
    assert [check["id"] for check in payload["checks"]] == ["chat", "dashboard-api"]
    assert payload["ready"] is True and payload["status"] == "ready" and payload["canChat"] is True
    docker = main._build_readiness_payload(
        service_statuses=status, loaded_model="qwen.gguf", context_size=8192,
        bootstrap_info=BootstrapStatus(active=False), host_agent={"available": False},
        stt_model_cached=None, stt_model_name="whisper",
    )
    assert "open-webui" in [check["id"] for check in docker["checks"]] and docker["ready"] is False


def test_status_reports_the_runtime(monkeypatch):
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "_build_api_status", _raise_os_error)
    response = TestClient(main.app).get("/api/status", headers={"Authorization": f"Bearer {main.security.DASHBOARD_API_KEY}"})
    assert response.json()["runtime"] == {"kind": "docker", "unavailable": []}


async def _raise_os_error():
    raise OSError("unavailable")


def test_native_external_links_offer_chat_not_raw_llama(monkeypatch):
    import main
    monkeypatch.setattr(main, "NATIVE", True)
    monkeypatch.setattr(main, "NATIVE_CHAT_PORT", 3010)
    monkeypatch.setattr(main, "SERVICES", {"llama-server": {"name": "llama", "port": 8080, "external_port": 8080}})
    links = asyncio.run(main.get_external_links(api_key="k"))
    assert [(link["id"], link["port"], link["label"]) for link in links] == [("chat", 3010, "Chat")]


# --- data directory ---------------------------------------------------------------------

def test_no_dashboard_api_module_falls_back_to_slash_data():
    # hermes_auth.py is the Hermes container's entrypoint, where /data is its own mount.
    offenders = []
    for path in API_DIR.rglob("*.py"):
        if "tests" in path.parts or path.name == "hermes_auth.py":
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""["']/data(/|["'])""", line):
                offenders.append(f"{path.name}:{number}")
    assert offenders == []


def test_generated_api_key_is_written_to_the_data_dir():
    source = (API_DIR / "security.py").read_text(encoding="utf-8")
    assert 'Path(DATA_DIR) / "dashboard-api-key.txt"' in source


# --- Windows metrics -----------------------------------------------------------------------

class FakeKernel32:
    def __init__(self, times, memory=(16 * 1024 ** 3, 4 * 1024 ** 3), ok=True):
        self.times = list(times)
        self.memory = memory
        self.ok = ok

    def GetSystemTimes(self, idle, kernel, user):
        values = self.times.pop(0)
        for ref, value in zip((idle, kernel, user), values):
            ref._obj.dwLowDateTime = value & 0xFFFFFFFF
            ref._obj.dwHighDateTime = value >> 32
        return self.ok

    def GlobalMemoryStatusEx(self, ref):
        assert ref._obj.dwLength == ctypes.sizeof(windows_metrics._MemoryStatusEx)
        ref._obj.ullTotalPhys, ref._obj.ullAvailPhys = self.memory
        return self.ok


def test_windows_cpu_load_is_a_delta_of_system_times():
    windows_metrics._reset_cpu_sample()
    # (idle, kernel incl. idle, user) in 100 ns ticks; the second sample is past 2**32.
    k32 = FakeKernel32([(1_000, 2_000, 1_000), (2**32 + 1_000, 2**32 + 2_000 + 3_000, 1_000 + 1_000)])
    first = windows_metrics.cpu_metrics(k32)
    assert first == {"percent": None, "temp_c": None, "scope": "host", "source": "windows-kernel32"}
    # busy = (kernel + user) - idle = (2**32 + 3000 + 1000) - 2**32 = 4000 of 2**32 + 4000.
    second = windows_metrics.cpu_metrics(k32)
    assert second["percent"] == round(4000 / (2**32 + 4000) * 100, 1)
    windows_metrics._reset_cpu_sample()
    busy = FakeKernel32([(0, 0, 0), (250, 500, 500)])
    windows_metrics.cpu_metrics(busy)
    assert windows_metrics.cpu_metrics(busy)["percent"] == 75.0


def test_windows_ram_is_total_minus_available():
    assert windows_metrics.ram_metrics(FakeKernel32([])) == {
        "used_gb": 12.0, "total_gb": 16.0, "percent": 75.0, "scope": "host", "source": "windows-kernel32"}


def test_windows_api_failures_raise():
    windows_metrics._reset_cpu_sample()
    with pytest.raises(OSError):
        windows_metrics.cpu_metrics(FakeKernel32([(0, 0, 0)], ok=False))
    with pytest.raises(OSError):
        windows_metrics.ram_metrics(FakeKernel32([], ok=False))
    with pytest.raises(ValueError):
        windows_metrics.ram_metrics(FakeKernel32([], memory=(1, 2)))


def test_helpers_use_windows_metrics_on_windows(monkeypatch):
    monkeypatch.setattr(helpers.platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_metrics, "cpu_metrics", lambda: {"percent": 12.5, "source": "windows-kernel32"})
    monkeypatch.setattr(windows_metrics, "ram_metrics", lambda: {"used_gb": 1.0, "source": "windows-kernel32"})
    assert helpers.get_cpu_metrics()["percent"] == 12.5
    assert helpers.get_ram_metrics()["used_gb"] == 1.0

    def failing():
        raise OSError("denied")
    monkeypatch.setattr(windows_metrics, "cpu_metrics", failing)
    assert helpers.get_cpu_metrics() == {"percent": None, "temp_c": None}
