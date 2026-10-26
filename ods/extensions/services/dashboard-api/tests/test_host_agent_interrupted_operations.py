"""Host agent: interrupted extension operations, and Remove deleting containers.

A WSL/Docker crash during an Open WebUI add left its card showing frozen
install progress for about an hour, and removing Excalidraw left its stopped
container behind (RTX Spark laptop, 2026-10-25). No test here runs Docker.
"""

import ast
import collections
import importlib.util
import io
import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

_agent_path = Path(__file__).resolve().parents[4] / "bin" / "ods-host-agent.py"
_spec = importlib.util.spec_from_file_location("ods_host_agent", _agent_path)
_mod = importlib.util.module_from_spec(_spec)
sys.modules["ods_host_agent"] = _mod
_spec.loader.exec_module(_mod)


class _Handler:
    """The parts of BaseHTTPRequestHandler the agent's handlers use."""

    def __init__(self, body: bytes = b"", path: str = "/"):
        self.headers = {"Authorization": "Bearer test-key", "Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.client_address = ("127.0.0.1", 12345)
        self.path = path
        self.response_code = None

    def send_response(self, code):
        self.response_code = code

    def send_header(self, *_args):
        pass

    def end_headers(self):
        pass

    def body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


@pytest.fixture
def agent(tmp_path, monkeypatch):
    data = tmp_path / "data"
    (data / "extension-progress").mkdir(parents=True)
    users = data / "user-extensions"
    users.mkdir()
    monkeypatch.setattr(_mod, "DATA_DIR", data)
    monkeypatch.setattr(_mod, "INSTALL_DIR", tmp_path)
    monkeypatch.setattr(_mod, "USER_EXTENSIONS_DIR", users)
    monkeypatch.setattr(_mod, "EXTENSIONS_DIR", tmp_path / "extensions" / "services")
    monkeypatch.setattr(_mod, "AGENT_API_KEY", "test-key")
    return data


def _record(data, service_id, status, **extra):
    path = data / "extension-progress" / f"{service_id}.json"
    path.write_text(json.dumps({
        "service_id": service_id, "status": status, "phase_label": "Downloading images...",
        "error": None, "started_at": "2026-10-25T10:00:00+00:00",
        "updated_at": "2026-10-25T10:03:15+00:00", **extra,
    }), encoding="utf-8")
    return path


def _age(path, seconds):
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_progress_records_name_the_agent_process(agent):
    _mod._write_progress("gotify", "pulling", "Downloading images...")
    record = json.loads((agent / "extension-progress" / "gotify.json").read_text(encoding="utf-8"))
    assert record["agent_instance"] == _mod._AGENT_INSTANCE
    assert len(_mod._AGENT_INSTANCE) == 16


@pytest.mark.parametrize("phase", ["pulling", "starting", "setup_hook"])
def test_startup_marks_an_earlier_process_operation_interrupted(agent, phase):
    path = _record(agent, "open-webui", phase, agent_instance="0123456789abcdef",
                   operation_id="a" * 32)

    assert _mod._interrupt_orphaned_progress() == ["open-webui"]

    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["status"] == "error"
    assert record["interrupted"] is True
    assert record["interrupted_phase"] == phase
    assert record["error"] == _mod.INTERRUPTED_PROGRESS_ERROR
    assert "Retry" in record["error"]
    # What the attempt was is kept, so the dashboard can retry the same thing.
    assert record["operation_id"] == "a" * 32
    assert record["started_at"] == "2026-10-25T10:00:00+00:00"
    assert record["agent_instance"] == _mod._AGENT_INSTANCE


@pytest.mark.parametrize("status", ["error", "started", "prepared", "installing", "complete"])
def test_startup_leaves_final_and_foreign_records_alone(agent, status):
    path = _record(agent, "gotify", status, agent_instance="0123456789abcdef")
    before = path.read_bytes()
    assert _mod._interrupt_orphaned_progress() == []
    assert path.read_bytes() == before


def test_startup_gives_a_fresh_dashboard_record_time_to_reach_the_agent(agent):
    # The dashboard writes "Starting installation..." itself, just before its
    # request; it carries no agent identity.
    fresh = _record(agent, "fresh", "pulling")
    stale = _record(agent, "stale", "pulling")
    _age(stale, _mod._DASHBOARD_PROGRESS_GRACE_SECONDS + 60)

    assert _mod._interrupt_orphaned_progress() == ["stale"]
    assert json.loads(fresh.read_text(encoding="utf-8"))["status"] == "pulling"
    assert json.loads(stale.read_text(encoding="utf-8"))["interrupted"] is True


def test_startup_skips_records_it_cannot_trust(agent):
    progress = agent / "extension-progress"
    (progress / "Bad.Name.json").write_text(json.dumps({"service_id": "Bad.Name", "status": "pulling"}))
    (progress / "renamed.json").write_text(json.dumps({"service_id": "other", "status": "pulling",
                                                        "agent_instance": "x"}))
    (progress / "broken.json").write_text("{not json")
    assert _mod._interrupt_orphaned_progress() == []


def test_live_jobs_cover_every_progress_writing_worker(monkeypatch):
    locks = {"gotify": threading.Lock(), "idle": threading.Lock(), "open-webui": threading.Lock()}
    locks["gotify"].acquire()
    locks["open-webui"].acquire()
    monkeypatch.setattr(_mod, "_service_locks", locks)
    monkeypatch.setattr(_mod, "_install_operation_live", {("bookstack", "b" * 32)})
    monkeypatch.setattr(_mod, "_opencode_setup_in_progress", lambda: True)
    assert _mod._live_extension_jobs() == ["bookstack", "gotify", "open-webui", "opencode"]


def test_live_jobs_count_an_opencode_setup_that_is_starting(monkeypatch):
    # Setup writes its first record under this lock, before its thread runs.
    class HeldLock:
        def acquire(self, timeout=-1):
            assert timeout == 2
            return False

        def release(self):
            raise AssertionError("never acquired")

    monkeypatch.setattr(_mod, "_service_locks", {})
    monkeypatch.setattr(_mod, "_install_operation_live", set())
    monkeypatch.setattr(_mod, "_opencode_setup_in_progress", lambda: False)
    monkeypatch.setattr(_mod, "_opencode_setup_lock", HeldLock())
    assert _mod._live_extension_jobs() == ["opencode"]


def test_jobs_endpoint_reads_the_clock_before_the_live_list(agent, monkeypatch):
    order: list[str] = []

    def now():
        order.append("now")
        return "2026-10-25T10:05:00+00:00"

    def live():
        order.append("live")
        return ["gotify"]

    monkeypatch.setattr(_mod, "_iso_now", now)
    monkeypatch.setattr(_mod, "_live_extension_jobs", live)
    handler = _Handler(path="/v1/extension/jobs")
    _mod.AgentHandler.do_GET(handler)
    assert handler.response_code == 200
    assert handler.body() == {"schema_version": 1, "instance": _mod._AGENT_INSTANCE,
                              "now": "2026-10-25T10:05:00+00:00", "live": ["gotify"]}
    # A record written after "now" may belong to an operation that started
    # after the live list was read; the dashboard does not judge it.
    assert order == ["now", "live"]


def test_jobs_endpoint_requires_the_agent_key(agent):
    handler = _Handler(path="/v1/extension/jobs")
    handler.headers["Authorization"] = "Bearer wrong"
    _mod.AgentHandler.do_GET(handler)
    assert handler.response_code in (401, 403)


def test_startup_marks_records_after_binding_and_before_serving():
    tree = ast.parse(_agent_path.read_text(encoding="utf-8"))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    calls = [node.func for node in ast.walk(main) if isinstance(node, ast.Call)]
    names = [(getattr(func, "id", None) or getattr(func, "attr", None)) for func in calls]
    lines = {name: next(func.lineno for func, other in zip(calls, names) if other == name)
             for name in ("_create_host_agent_server", "_interrupt_orphaned_progress", "serve_forever")}
    assert lines["_create_host_agent_server"] < lines["_interrupt_orphaned_progress"] < lines["serve_forever"]


# --- Remove deletes the extension's stopped containers ---


def _library_extension(users, service_id, compose_name, compose):
    directory = users / service_id
    directory.mkdir()
    (directory / "manifest.yaml").write_text(f"service:\n  id: {service_id}\n", encoding="utf-8")
    (directory / compose_name).write_text(compose, encoding="utf-8")
    return directory


@pytest.fixture
def removal_helper(agent, tmp_path):
    """An installed stop helper that records the removal it was asked for."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "stop-owned-containers.py").write_text(
        "import json\n"
        "def remove_owned_containers(root, services, remove_volumes=False):\n"
        "    (root / 'removal.json').write_text(json.dumps([services, remove_volumes]))\n"
        "    return ['ods-' + name for name in services], (['v'] if remove_volumes else []), ['kept']\n",
        encoding="utf-8",
    )
    return tmp_path / "removal.json"


def _remove(body):
    handler = _Handler(json.dumps(body).encode("utf-8"))
    _mod.AgentHandler._handle_extension_remove_containers(handler)
    return handler


def test_remove_deletes_the_extension_and_its_own_companions(agent, removal_helper):
    users = agent / "user-extensions"
    _library_extension(users, "bookstack", "compose.yaml.disabled",
                       "services:\n  bookstack:\n    image: a\n  bookstack-db:\n    image: b\n"
                       "  bookstack-search:\n    image: d\n  searxng:\n    image: c\n")
    # A companion with its own extension keeps its own lifecycle.
    _library_extension(users, "bookstack-search", "compose.yaml.disabled", "services: {}\n")
    handler = _remove({"service_id": "bookstack"})
    assert handler.response_code == 200
    assert handler.body() == {"status": "ok", "service_id": "bookstack",
                              "containers": ["ods-bookstack", "ods-bookstack-db"],
                              "volumes_removed": [], "volumes_kept": ["kept"]}
    assert json.loads(removal_helper.read_text()) == [["bookstack", "bookstack-db"], False]


def test_remove_deletes_volumes_only_when_asked(agent, removal_helper):
    _library_extension(agent / "user-extensions", "gotify", "compose.yaml.disabled",
                       "services:\n  gotify:\n    image: a\n")
    handler = _remove({"service_id": "gotify", "delete_volumes": True})
    assert handler.response_code == 200
    assert handler.body()["volumes_removed"] == ["v"]
    assert json.loads(removal_helper.read_text()) == [["gotify"], True]


def test_remove_refuses_an_enabled_extension(agent, removal_helper):
    _library_extension(agent / "user-extensions", "gotify", "compose.yaml",
                       "services:\n  gotify:\n    image: a\n")
    handler = _remove({"service_id": "gotify"})
    assert handler.response_code == 409
    assert "disable it before removing it" in handler.body()["error"]
    assert not removal_helper.exists()


def test_remove_refuses_a_bundled_extension(agent, removal_helper):
    bundled = agent.parent / "extensions" / "services" / "n8n"
    bundled.mkdir(parents=True)
    (bundled / "manifest.yaml").write_text("service:\n  id: n8n\n", encoding="utf-8")
    (bundled / "compose.yaml.disabled").write_text("services:\n  n8n:\n    image: a\n", encoding="utf-8")
    handler = _remove({"service_id": "n8n"})
    assert handler.response_code == 409
    assert "not an installed Library extension" in handler.body()["error"]
    assert not removal_helper.exists()


def test_remove_rejects_a_non_boolean_data_choice(agent, removal_helper):
    _library_extension(agent / "user-extensions", "gotify", "compose.yaml.disabled",
                       "services:\n  gotify:\n    image: a\n")
    handler = _remove({"service_id": "gotify", "delete_volumes": "yes"})
    assert handler.response_code == 400
    assert not removal_helper.exists()


def test_remove_waits_for_another_operation_on_the_service(agent, removal_helper, monkeypatch):
    _library_extension(agent / "user-extensions", "gotify", "compose.yaml.disabled",
                       "services:\n  gotify:\n    image: a\n")
    busy = threading.Lock()
    busy.acquire()
    monkeypatch.setattr(_mod, "_service_locks", {"gotify": busy})
    handler = _remove({"service_id": "gotify"})
    assert handler.response_code == 409
    assert not removal_helper.exists()


def test_remove_reports_a_refusal_from_docker_and_releases_the_service(agent, tmp_path, monkeypatch):
    _library_extension(agent / "user-extensions", "gotify", "compose.yaml.disabled",
                       "services:\n  gotify:\n    image: a\n")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "stop-owned-containers.py").write_text(
        "def remove_owned_containers(root, services, remove_volumes=False):\n"
        "    raise ValueError('The gotify container is running; stop it before removing it')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_mod, "_service_locks", collections.defaultdict(threading.Lock))
    handler = _remove({"service_id": "gotify"})
    assert handler.response_code == 409
    assert "stop it before removing it" in handler.body()["error"]
    assert not _mod._service_locks["gotify"].locked()


def test_remove_falls_back_to_the_own_container_for_an_unreadable_definition(agent, removal_helper):
    _library_extension(agent / "user-extensions", "gotify", "compose.yaml.disabled", "services: [\n")
    handler = _remove({"service_id": "gotify"})
    assert handler.response_code == 200
    assert json.loads(removal_helper.read_text()) == [["gotify"], False]
