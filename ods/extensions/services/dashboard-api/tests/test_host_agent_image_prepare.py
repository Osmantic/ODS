"""Image work that outlasts a request: prepare-images, the streaming pull and build.

On the Mac mini's slow link (2026-10-04) Hermes' first `docker compose up`
spent its whole 600 s start allowance downloading, and adding Open WebUI died
at 600 s with the 0.11.4 image unfinished, while the Dashboard had given up at
180 s. Bundled services now download their images first through
/v1/extension/prepare-images, before anything is selected, with progress on
the card; the pull stops only when Docker stops making progress.

On Workstation06 (2026-10-26) ComfyUI's first NVIDIA image build took the
whole 600 s start allowance (ten minutes, 0% cached), so the start timed out
and the card showed ComfyUI stopped. Images a bundled service builds from its
shipped Dockerfile are now built in the same step, with the build step on the
card.
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


class TimedBuild(FakePull):
    """`docker compose build`: prints its lines at once, then runs until the faked clock reaches ``exit_at``."""

    def __init__(self, command, clock, exit_at, lines=(), returncode=0):
        super().__init__(command, lines=lines, returncode=returncode)
        self._clock = clock
        self._exit_at = exit_at

    def poll(self):
        if self._clock[0] < self._exit_at and not self.killed:
            return None
        return super().poll()


@pytest.mark.parametrize("lines, detail", [
    (["#5 [comfyui  1/15] FROM docker.io/nvidia/cuda:12.8.0-runtime-ubuntu22.04\n",
      "#14 [comfyui 10/15] RUN pip3 install --no-cache-dir comfy-kitchen\n",
      "#14 12.31 Collecting comfy-kitchen\n"], "step 10 of 15"),
    # Compose 5.2 names the target in the export line; its sub-lines do not.
    (["#19 [comfyui 15/15] WORKDIR /opt/comfyui\n", "#20 [comfyui] exporting to image\n",
      "#20 exporting layers 125.3s done\n"], "saving the image"),
])
def test_build_reports_its_step_and_uses_the_compose_files_up_uses(host, monkeypatch, clock, lines, detail):  # noqa: F811
    started = []

    def popen(command, **options):
        started.append((list(command), options))
        return TimedBuild(command, clock, clock[0] + 40, lines=lines)

    monkeypatch.setattr(_mod.subprocess, "Popen", popen)
    written = []
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: written.append(args))

    ok, error = _mod._build_compose_images(["-f", "base.yml"], ["comfyui"], "comfyui")

    assert (ok, error) == (True, "")
    command, options = started[0]
    assert command == ["docker", "compose", "--progress", "plain", "-f", "base.yml", "build", "comfyui"]
    assert options["stderr"] is subprocess.STDOUT
    labels = [args[2] for args in written if args[:2] == ("comfyui", "pulling")]
    assert labels[0] == "Building images..."
    assert f"Building images... 0:15 elapsed, {detail}" in labels


def test_build_stops_when_buildkit_reports_nothing(host, monkeypatch, clock):  # noqa: F811
    monkeypatch.setattr(_mod, "IMAGE_PULL_STALL_SECONDS", 120)
    monkeypatch.setattr(_mod, "IMAGE_PULL_MAX_SECONDS", 10 ** 6)
    build = FakePull(["cmd"], hang=True)
    monkeypatch.setattr(_mod.subprocess, "Popen", lambda command, **options: build)
    written = []
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: written.append(args))

    ok, error = _mod._build_compose_images([], ["comfyui"], "comfyui")

    assert ok is False and build.killed
    assert error == "Image build made no progress for 2 minutes."
    # Before the first Dockerfile step there is no step to name.
    assert ("comfyui", "pulling", "Building images... 0:15 elapsed") in written


def test_failed_build_leads_with_buildkits_decisive_error_without_credentials(host, monkeypatch, clock):  # noqa: F811
    _popen(monkeypatch, returncode=1, lines=[
        "#14 [comfyui 10/15] RUN pip3 install comfy-kitchen\n",
        "#14 0.52 token SERVICE_API_KEY=persisted-credential\n",
        "#14 ERROR: process did not complete successfully: exit code: 1\n",
        'failed to solve: process "/bin/sh -c pip3 install comfy-kitchen" did not complete successfully: exit code: 1\n',
    ])
    monkeypatch.setattr(_mod, "_write_progress", lambda *args, **kwargs: None)

    ok, error = _mod._build_compose_images([], ["comfyui"], "comfyui")

    assert ok is False
    assert error.startswith('Untrusted build error: failed to solve: process "/bin/sh -c pip3 install')
    assert "#14 [comfyui 10/15] RUN pip3 install comfy-kitchen" in error
    assert "persisted-credential" not in error


def test_missing_images_names_pulls_and_local_builds_apart(host, monkeypatch):  # noqa: F811
    config = {"name": "ods", "services": {
        "hermes": {"image": "nousresearch/hermes-agent@sha256:" + "a" * 64},
        "hermes-proxy": {"image": "caddy:2"},
        "builder": {"image": "ods-built:local", "build": {"context": "."}},
        # No image: Compose names what `up` builds <project>-<service>.
        "comfyui": {"build": {"context": "./extensions/services/comfyui"}},
        "token-spy": {"build": {"context": "./extensions/services/token-spy"}},
    }}
    present = {"caddy:2", "ods-token-spy"}
    seen = []

    def run(command, **kwargs):
        seen.append(list(command))
        if command[-3:] == ["config", "--format", "json"]:
            assert kwargs["env"] == {"ENABLE_OPEN_WEBUI": "true"}
            return types.SimpleNamespace(returncode=0, stdout=json.dumps(config), stderr="")
        if command[:3] == ["docker", "image", "inspect"]:
            return types.SimpleNamespace(returncode=0 if command[3] in present else 1, stdout="", stderr="")
        raise AssertionError(command)

    monkeypatch.setattr(_mod.subprocess, "run", run)
    pulls, builds = _mod._services_missing_images(
        ["-f", "b.yml"], ["hermes", "hermes-proxy", "builder", "comfyui", "token-spy"],
        {"ENABLE_OPEN_WEBUI": "true"})
    assert (pulls, builds) == (["hermes"], ["builder", "comfyui"])
    assert ["docker", "image", "inspect", "ods-comfyui"] in seen


@pytest.mark.parametrize("config, message", [
    ({"name": "ods", "services": {}}, "does not define hermes"),
    # Without the project name a build-only service's image cannot be named.
    ({"services": {"hermes": {"build": {"context": "."}}}}, "Invalid Compose configuration"),
])
def test_missing_images_refuses_a_configuration_it_cannot_read(host, monkeypatch, config, message):  # noqa: F811
    monkeypatch.setattr(_mod.subprocess, "run", lambda command, **kwargs: types.SimpleNamespace(
        returncode=0, stdout=json.dumps(config), stderr=""))
    with pytest.raises(RuntimeError, match=message):
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
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ([], []))
    monkeypatch.setattr(_mod, "_pull_compose_images", lambda *args, **kwargs: pytest.fail("no download expected"))

    status, response = prepare({"service_ids": ["hermes", "hermes-proxy"], "progress_id": "hermes"})

    assert status == 200 and response["status"] == "ready"
    assert not _mod._service_locks["hermes"].locked() and not _mod._service_locks["hermes-proxy"].locked()


def test_prepare_downloads_in_the_background_and_records_the_result(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: (["hermes"], []))
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
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: (["open-webui"], []))
    monkeypatch.setattr(_mod, "_pull_compose_images",
                        lambda *args, **kwargs: (False, "Image download made no progress for 15 minutes."))

    status, _response = prepare({"service_ids": ["open-webui"], "progress_id": "open-webui"})

    assert status == 202
    record = host.progress("open-webui")
    assert record["status"] == "error"
    assert record["error"] == "Image download made no progress for 15 minutes."
    assert not _mod._service_locks["open-webui"].locked()


def test_prepare_records_a_failed_build_for_the_card(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ([], ["hermes"]))
    monkeypatch.setattr(_mod, "_pull_compose_images", lambda *args, **kwargs: pytest.fail("nothing to download"))
    monkeypatch.setattr(_mod, "_build_compose_images",
                        lambda *args, **kwargs: (False, "Untrusted build error: failed to solve"))

    status, response = prepare({"service_ids": ["hermes"], "progress_id": "hermes"})

    # A missing local image alone is not ready: `up` would build it inside the start.
    assert status == 202 and (response["pulling"], response["building"]) == ([], ["hermes"])
    record = host.progress("hermes")
    assert (record["status"], record["phase_label"]) == ("error", "Image build failed")
    assert record["error"] == "Untrusted build error: failed to solve"
    assert not _mod._service_locks["hermes"].locked()


def test_prepare_refuses_while_the_service_is_being_changed(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: pytest.fail("not reached"))
    _mod._service_locks["hermes-proxy"].acquire()
    try:
        status, response = prepare({"service_ids": ["hermes", "hermes-proxy"], "progress_id": "hermes"})
    finally:
        _mod._service_locks["hermes-proxy"].release()
    assert status == 409
    assert not _mod._service_locks["hermes"].locked()


PACK_FILE = "model.safetensors"


@pytest.fixture
def model_pack(prepare, host, monkeypatch):  # noqa: F811
    """A bundled extension that ships a default model pack (as ComfyUI does)."""
    _mod._service_locks.pop("painter", None)
    directory = host.builtins / "painter"
    directory.mkdir()
    (directory / "compose.yaml.disabled").write_text(
        "services:\n  painter:\n    image: example/painter:1\n", encoding="utf-8")
    (directory / "model-packs.json").write_text(json.dumps({
        "schema": "ods.model-packs.v1",
        "model_root": {"nvidia": "data/painter/models"},
        "packs": [{"id": "image-default", "label": "Painter model", "default": True,
                   "files": [{"subdir": "checkpoints", "file": PACK_FILE,
                              "url": "https://example.invalid/model.safetensors",
                              "sha256": "a" * 64, "bytes": 5, "license": "test"}]}],
    }), encoding="utf-8")
    monkeypatch.setattr(_mod, "GPU_BACKEND", "nvidia")
    monkeypatch.setattr(_mod._extension_assets, "check_space", lambda files: None)
    return host.install_dir / "data/painter/models/checkpoints" / PACK_FILE


def test_prepare_fetches_a_missing_model_pack_after_the_images(prepare, host, monkeypatch, model_pack):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: (["painter"], []))
    order = []

    def pull(*args, **kwargs):
        order.append("images")
        return True, ""

    monkeypatch.setattr(_mod, "_pull_compose_images", pull)
    monkeypatch.setattr(_mod, "_build_compose_images", lambda *args, **kwargs: pytest.fail("nothing to build"))

    def fetch(item, report):
        order.append("model")
        assert _mod._service_locks["painter"].locked()  # one operation per service
        item.target.parent.mkdir(parents=True)
        item.target.write_bytes(b"model")
        report(5)

    monkeypatch.setattr(_mod._extension_assets, "fetch", fetch)

    status, response = prepare({"service_ids": ["painter"], "progress_id": "painter"})

    assert status == 202 and response["models"] == [PACK_FILE]
    assert order == ["images", "model"]
    assert model_pack.read_bytes() == b"model"
    record = host.progress("painter")
    assert (record["status"], record["phase_label"]) == ("prepared", "Images and models downloaded")
    assert not _mod._service_locks["painter"].locked()


def test_prepare_builds_local_images_after_downloads_and_before_models(prepare, host, monkeypatch, model_pack):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: (["hermes"], ["painter"]))
    order = []

    def pull(flags, services, progress_id, *, env=None):
        order.append(("pull", services))
        return True, ""

    def build(flags, services, progress_id, *, env=None):
        order.append(("build", services))
        # The files and selectors the enable will run, under the card's record.
        assert (flags, progress_id, env) == (["-f", "base.yml"], "painter", {"E": "1"})
        assert _mod._service_locks["painter"].locked()  # one operation per service
        return True, ""

    def fetch(item, report):
        order.append(("model", item.target.name))
        item.target.parent.mkdir(parents=True)
        item.target.write_bytes(b"model")

    monkeypatch.setattr(_mod, "_pull_compose_images", pull)
    monkeypatch.setattr(_mod, "_build_compose_images", build)
    monkeypatch.setattr(_mod._extension_assets, "fetch", fetch)

    status, response = prepare({"service_ids": ["hermes", "painter"], "progress_id": "painter"})

    assert status == 202
    assert (response["pulling"], response["building"]) == (["hermes"], ["painter"])
    assert order == [("pull", ["hermes"]), ("build", ["painter"]), ("model", PACK_FILE)]
    record = host.progress("painter")
    assert (record["status"], record["phase_label"]) == ("prepared", "Images built and models downloaded")
    assert not _mod._service_locks["painter"].locked()


def test_prepare_downloads_a_missing_model_even_when_the_images_are_here(prepare, host, monkeypatch, model_pack):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ([], []))
    monkeypatch.setattr(_mod, "_pull_compose_images", lambda *args, **kwargs: pytest.fail("no image download"))
    labels = []
    real_write = _mod._write_progress

    def recording_write(service_id, status, label="", **kw):
        labels.append(label)
        real_write(service_id, status, label, **kw)

    monkeypatch.setattr(_mod, "_write_progress", recording_write)
    monkeypatch.setattr(_mod._extension_assets, "fetch", lambda item, report: (
        item.target.parent.mkdir(parents=True), item.target.write_bytes(b"model")))

    status, response = prepare({"service_ids": ["painter"], "progress_id": "painter"})

    assert status == 202 and response["pulling"] == []
    assert labels[0] == "Downloading the Painter model..."
    assert host.progress("painter")["status"] == "prepared"


def test_prepare_is_ready_when_images_and_models_are_here(prepare, host, monkeypatch, model_pack):  # noqa: F811
    model_pack.parent.mkdir(parents=True)
    model_pack.write_bytes(b"model")  # the pinned size: 5 bytes
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ([], []))
    monkeypatch.setattr(_mod._extension_assets, "fetch", lambda *args: pytest.fail("no download expected"))

    status, response = prepare({"service_ids": ["painter"], "progress_id": "painter"})

    assert status == 200 and response["status"] == "ready"
    assert not _mod._service_locks["painter"].locked()


def test_prepare_records_a_failed_model_download_for_the_card(prepare, host, monkeypatch, model_pack):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ([], []))

    def fail(item, report):
        raise _mod._extension_assets.AssetError("The download of Painter model stopped (timed out).")

    monkeypatch.setattr(_mod._extension_assets, "fetch", fail)

    status, _response = prepare({"service_ids": ["painter"], "progress_id": "painter"})

    assert status == 202
    record = host.progress("painter")
    assert (record["status"], record["phase_label"]) == ("error", "Model download failed")
    assert record["error"] == "The download of Painter model stopped (timed out)."
    assert not _mod._service_locks["painter"].locked()


def test_prepare_reports_a_malformed_pack_list_without_downloading(prepare, host, monkeypatch, model_pack):  # noqa: F811
    (host.builtins / "painter" / "model-packs.json").write_text('{"schema": "other"}', encoding="utf-8")
    monkeypatch.setattr(_mod, "_services_missing_images", lambda flags, ids, env: ([], []))

    status, response = prepare({"service_ids": ["painter"], "progress_id": "painter"})

    assert status == 503 and "schema must be ods.model-packs.v1" in response["error"]
    assert not _mod._service_locks["painter"].locked()


def test_prepare_reports_an_unresolvable_stack_without_downloading(prepare, host, monkeypatch):  # noqa: F811
    monkeypatch.setattr(_mod, "_services_missing_images",
                        lambda flags, ids, env: (_ for _ in ()).throw(RuntimeError("Could not resolve")))
    status, response = prepare({"service_ids": ["hermes"], "progress_id": "hermes"})
    assert status == 503 and response["error"] == "Could not resolve"
    assert not _mod._service_locks["hermes"].locked()
