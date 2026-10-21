"""Image downloads that outlast a request: prepare-images and the streaming pull.

On the Mac mini's slow link (2026-10-04) Hermes' first `docker compose up`
spent its whole 600 s start allowance downloading, and adding Open WebUI died
at 600 s with the 0.11.4 image unfinished, while the Dashboard had given up at
180 s. Bundled services now download their images first through
/v1/extension/prepare-images, before anything is selected, with progress on
the card; the pull stops only when Docker stops making progress.
"""

import json
import subprocess
import types

import pytest

from test_host_agent_install_rollback import _mod, host  # noqa: F401  (fixture)


class FakePull:
    """Stands in for `docker compose pull`: emits its lines, then exits."""

    def __init__(self, command, lines=(), returncode=0, hang=False):
        self.args = command
        self.stdout = iter(lines)
        self.returncode = None
        self._exit = returncode
        self._hang = hang
        self.killed = False

    def poll(self):
        if self._hang and not self.killed:
            return None
        self.returncode = -9 if self.killed else self._exit
        return self.returncode

    def wait(self, timeout=None):
        return self.poll()

    def kill(self):
        self.killed = True


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(_mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(_mod.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    return now


def _popen(monkeypatch, **kwargs):
    started = []

    def popen(command, **options):
        started.append((list(command), options))
        return FakePull(command, **kwargs)

    monkeypatch.setattr(_mod.subprocess, "Popen", popen)
    return started


def test_pull_reports_progress_and_uses_the_compose_files_up_uses(host, monkeypatch, clock):  # noqa: F811
    started = _popen(monkeypatch, lines=[" abc Pulling fs layer\n", " abc Pull complete\n",
                                         " def Already exists\n"])
    written = []
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: written.append(args))

    ok, error = _mod._pull_compose_images(["-f", "base.yml"], ["hermes", "hermes-proxy"], "hermes")

    assert (ok, error) == (True, "")
    command, options = started[0]
    assert command == ["docker", "compose", "--progress", "plain", "-f", "base.yml",
                       "pull", "hermes", "hermes-proxy"]
    assert options["stderr"] is subprocess.STDOUT
    assert written[0] == ("hermes", "pulling", "Downloading images...")


def test_pull_keeps_going_while_docker_reports_progress_and_stops_when_it_stalls(host, monkeypatch, clock):  # noqa: F811
    monkeypatch.setattr(_mod, "IMAGE_PULL_STALL_SECONDS", 120)
    monkeypatch.setattr(_mod, "IMAGE_PULL_MAX_SECONDS", 10 ** 6)
    pull = FakePull(["cmd"], hang=True)
    monkeypatch.setattr(_mod.subprocess, "Popen", lambda command, **options: pull)
    written = []
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: written.append(args))

    ok, error = _mod._pull_compose_images([], ["hermes"], "hermes")

    assert ok is False and pull.killed
    assert error == "Image download made no progress for 2 minutes."
    # While it waited, the card got an elapsed-time update every 15 s.
    labels = [args[2] for args in written if args[1] == "pulling"]
    assert any(label.startswith("Downloading images... 1:") for label in labels)


def test_pull_stops_at_the_overall_cap(host, monkeypatch, clock):  # noqa: F811
    monkeypatch.setattr(_mod, "IMAGE_PULL_STALL_SECONDS", 10 ** 6)
    monkeypatch.setattr(_mod, "IMAGE_PULL_MAX_SECONDS", 3 * 3600)
    pull = FakePull(["cmd"], hang=True)
    monkeypatch.setattr(_mod.subprocess, "Popen", lambda command, **options: pull)
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: None)

    ok, error = _mod._pull_compose_images([], ["hermes"], "hermes")

    assert ok is False and pull.killed
    assert error == "Image download did not finish within 3 hours."


def test_failed_pull_returns_dockers_last_words(host, monkeypatch, clock):  # noqa: F811
    _popen(monkeypatch, lines=[" Image x Pulling\n", "toomanyrequests: rate limit\n"], returncode=1)
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: None)

    ok, error = _mod._pull_compose_images([], ["hermes"], "hermes")

    assert ok is False
    assert error.splitlines() == ["Image download failed:", " Image x Pulling", "toomanyrequests: rate limit"]


def test_missing_images_skips_local_builds_and_names_the_services(host, monkeypatch):  # noqa: F811
    config = {"services": {
        "hermes": {"image": "nousresearch/hermes-agent@sha256:" + "a" * 64},
        "hermes-proxy": {"image": "caddy:2"},
        "builder": {"image": "ods-built:local", "build": {"context": "."}},
    }}
    seen = []

    def run(command, **kwargs):
        seen.append(list(command))
        if command[-3:] == ["config", "--format", "json"]:
            assert kwargs["env"] == {"ENABLE_OPEN_WEBUI": "true"}
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(config), stderr="")
        if command[:3] == ["docker", "image", "inspect"]:
            return types.SimpleNamespace(returncode=0 if command[3] == "caddy:2" else 1, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(_mod.subprocess, "run", run)
    missing = _mod._services_missing_images(["-f", "b.yml"], ["hermes", "hermes-proxy", "builder"],
                                            {"ENABLE_OPEN_WEBUI": "true"})
    assert missing == ["hermes"]
    assert ["docker", "image", "inspect", "ods-built:local"] not in seen


def test_missing_images_refuses_a_service_the_stack_does_not_define(host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod.subprocess, "run", lambda command, **kwargs: types.SimpleNamespace(
        returncode=0, stdout=json.dumps({"services": {}}), stderr=""))
    with pytest.raises(RuntimeError, match="does not define hermes"):
        _mod._services_missing_images([], ["hermes"], {})


def test_resolver_preview_assumes_bundled_services_and_selects_open_webui(host, monkeypatch):  # noqa: F811
    resolver = host.install_dir / "scripts" / "resolve-compose-stack.sh"
    resolver.write_text("#!/bin/sh\n", encoding="utf-8")
    (host.install_dir / ".env").write_text("ODS_MODE=local\nENABLE_OPEN_WEBUI=false\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "_find_usable_bash", lambda: "bash")
    calls = []

    def run(command, **kwargs):
        calls.append((list(command), kwargs.get("env") or {}))
        return types.SimpleNamespace(returncode=0, stdout="-f docker-compose.base.yml\n", stderr="")

    monkeypatch.setattr(_mod.subprocess, "run", run)
    flags, env = _mod._image_prepare_context(["hermes", "hermes-proxy", "open-webui"])

    command, resolver_env = calls[0]
    assert command[command.index("--assume-enabled") + 1] == "hermes,hermes-proxy"
    assert "--gpu-count" in command
    assert resolver_env["ENABLE_OPEN_WEBUI"] == "true"
    assert env["ENABLE_OPEN_WEBUI"] == "true"
    assert flags == ["-f", "docker-compose.base.yml"]
    # Nothing was selected: the installed choice is unchanged.
    assert "ENABLE_OPEN_WEBUI=false" in (host.install_dir / ".env").read_text(encoding="utf-8")


@pytest.fixture
def prepare(host, monkeypatch):  # noqa: F811
    for service_id in ("hermes", "hermes-proxy", "open-webui"):
        _mod._service_locks.pop(service_id, None)
    for service_id in ("hermes", "hermes-proxy"):
        directory = host.builtins / service_id
        directory.mkdir()
        (directory / "compose.yaml.disabled").write_text(
            f"services:\n  {service_id}:\n    image: example/{service_id}:1\n", encoding="utf-8")
    monkeypatch.setattr(_mod, "_image_prepare_context", lambda ids: (["-f", "base.yml"], {"E": "1"}))

    def request(body):
        monkeypatch.setattr(_mod, "read_json_body", lambda handler: body)
        _mod.AgentHandler._handle_prepare_images(object())
        return host.responses[-1]

    return request


@pytest.mark.parametrize("body", [
    {"service_ids": [], "progress_id": "hermes"},
    {"service_ids": ["hermes"], "progress_id": "hermes-proxy"},
    {"service_ids": ["hermes", "hermes"], "progress_id": "hermes"},
    {"service_ids": ["Hermes"], "progress_id": "Hermes"},
    {"service_ids": "hermes", "progress_id": "hermes"},
    {"service_ids": ["nousresearch/hermes-agent:latest"], "progress_id": "nousresearch/hermes-agent:latest"},
])
def test_prepare_accepts_only_distinct_service_ids(prepare, body):
    status, response = prepare(body)
    assert status == 400


def test_prepare_refuses_library_and_unknown_services(prepare, host):  # noqa: F811
    host.extension("gotify")  # a Library recipe under data/user-extensions
    status, response = prepare({"service_ids": ["gotify"], "progress_id": "gotify"})
    assert status == 400 and "bundled" in response["error"]
    status, response = prepare({"service_ids": ["not-installed"], "progress_id": "not-installed"})
    assert status == 400


def test_prepare_answers_ready_without_downloading_when_images_are_here(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: [])
    monkeypatch.setattr(_mod, "_pull_compose_images", lambda *args, **kwargs: pytest.fail("no download expected"))

    status, response = prepare({"service_ids": ["hermes", "hermes-proxy"], "progress_id": "hermes"})

    assert status == 200 and response["status"] == "ready"
    assert not _mod._service_locks["hermes"].locked() and not _mod._service_locks["hermes-proxy"].locked()


def test_prepare_downloads_in_the_background_and_records_the_result(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ["hermes"])
    pulls = []

    def pull(flags, services, progress_id, *, env=None):
        pulls.append((flags, services, progress_id, env))
        assert _mod._service_locks["hermes"].locked()  # one operation per service
        return True, ""

    monkeypatch.setattr(_mod, "_pull_compose_images", pull)

    status, response = prepare({"service_ids": ["hermes", "hermes-proxy"], "progress_id": "hermes"})

    assert status == 202 and response["pulling"] == ["hermes"]
    assert pulls == [(["-f", "base.yml"], ["hermes"], "hermes", {"E": "1"})]
    record = host.progress("hermes")
    assert (record["status"], record["phase_label"]) == ("prepared", "Images downloaded")
    assert not _mod._service_locks["hermes"].locked()


def test_prepare_records_a_failed_download_for_the_card(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ["open-webui"])
    monkeypatch.setattr(_mod, "_pull_compose_images",
                        lambda *args, **kwargs: (False, "Image download made no progress for 15 minutes."))

    status, _response = prepare({"service_ids": ["open-webui"], "progress_id": "open-webui"})

    assert status == 202
    record = host.progress("open-webui")
    assert record["status"] == "error"
    assert record["error"] == "Image download made no progress for 15 minutes."
    assert not _mod._service_locks["open-webui"].locked()


def test_prepare_refuses_while_the_service_is_being_changed(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: pytest.fail("not reached"))
    _mod._service_locks["hermes-proxy"].acquire()
    try:
        status, response = prepare({"service_ids": ["hermes", "hermes-proxy"], "progress_id": "hermes"})
    finally:
        _mod._service_locks["hermes-proxy"].release()
    assert status == 409
    assert not _mod._service_locks["hermes"].locked()


def test_prepare_reports_an_unresolvable_stack_without_downloading(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images",
                        lambda flags, ids, env: (_ for _ in ()).throw(RuntimeError("Could not resolve")))
    status, response = prepare({"service_ids": ["hermes"], "progress_id": "hermes"})
    assert status == 503 and response["error"] == "Could not resolve"
    assert not _mod._service_locks["hermes"].locked()
