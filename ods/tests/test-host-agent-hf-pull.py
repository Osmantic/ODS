"""Unit tests for the host agent's shared download helpers, extracted for
reuse between the catalog-gated GGUF downloader (_handle_model_download) and
the new direct-from-Hugging-Face puller (_handle_model_pull_hf).

Endpoint-level request validation (repo_id/filename/target shape) is covered
in dashboard-api's tests/test_models.py against the mirrored Pydantic
validators; this file covers the download machinery itself, which is the
part unique to the host agent.

Run with: pytest tests/test-host-agent-hf-pull.py
"""
import hashlib
import importlib.util
import os

import pytest

AGENT_PATH = os.path.join(os.path.dirname(__file__), "../bin/ods-host-agent.py")

spec = importlib.util.spec_from_file_location("ods_host_agent_hf_pull", AGENT_PATH)
agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent)


@pytest.fixture(autouse=True)
def clean_download_state(monkeypatch):
    agent._model_download_cancel.clear()
    monkeypatch.setattr(agent._model_download_cancel, "wait", lambda timeout: None)
    yield
    agent._model_download_cancel.clear()


class FakeProc:
    def __init__(self, returncode=0, write_bytes=None, tmp_path=None):
        self.returncode = returncode
        if write_bytes is not None and tmp_path is not None:
            tmp_path.write_bytes(write_bytes)

    def wait(self, timeout=None):
        return self.returncode

    def kill(self):
        pass


def test_curl_head_content_length_parses_last_header(monkeypatch):
    class FakeResult:
        stdout = (
            "HTTP/1.1 302 Found\r\nContent-Length: 12\r\n\r\n"
            "HTTP/1.1 200 OK\r\nContent-Length: 123456789\r\n\r\n"
        )

    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: FakeResult())

    assert agent._curl_head_content_length("https://example.com/model.gguf") == 123456789


def test_curl_head_content_length_ignores_small_redirect_page_sizes(monkeypatch):
    class FakeResult:
        stdout = "HTTP/1.1 200 OK\r\nContent-Length: 500\r\n\r\n"

    monkeypatch.setattr(agent.subprocess, "run", lambda *a, **k: FakeResult())

    assert agent._curl_head_content_length("https://example.com/model.gguf") == 0


def test_curl_head_content_length_returns_zero_on_timeout(monkeypatch):
    def raise_timeout(*a, **k):
        raise agent.subprocess.TimeoutExpired(cmd="curl", timeout=30)

    monkeypatch.setattr(agent.subprocess, "run", raise_timeout)

    assert agent._curl_head_content_length("https://example.com/model.gguf") == 0


def test_download_one_file_succeeds_and_renames_into_place(monkeypatch, tmp_path):
    target = tmp_path / "model.gguf"
    status_path = tmp_path / "status.json"

    def fake_popen(cmd, **kwargs):
        out_index = cmd.index("-o") + 1
        return FakeProc(returncode=0, write_bytes=b"fake model bytes", tmp_path=type(target)(cmd[out_index]))

    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)

    outcome, err = agent._download_one_file(
        "https://example.com/model.gguf", target, status_path, "model.gguf",
    )

    assert outcome == "success"
    assert err == ""
    assert target.read_bytes() == b"fake model bytes"
    assert not target.with_name("model.gguf.part").exists()


def test_download_one_file_fails_after_three_attempts(monkeypatch, tmp_path):
    target = tmp_path / "model.gguf"
    status_path = tmp_path / "status.json"
    attempts = {"n": 0}

    def fake_popen(cmd, **kwargs):
        attempts["n"] += 1
        return FakeProc(returncode=1)

    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)

    outcome, err = agent._download_one_file(
        "https://example.com/model.gguf", target, status_path, "model.gguf",
    )

    assert outcome == "failed"
    assert attempts["n"] == 3
    assert "curl exited with code 1" in err
    assert not target.exists()
    assert not target.with_name("model.gguf.part").exists()


def test_download_one_file_honors_cancel_before_starting(monkeypatch, tmp_path):
    target = tmp_path / "model.gguf"
    status_path = tmp_path / "status.json"
    agent._model_download_cancel.set()

    def fake_popen(cmd, **kwargs):
        raise AssertionError("must not start curl once cancelled")

    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)

    outcome, err = agent._download_one_file(
        "https://example.com/model.gguf", target, status_path, "model.gguf",
    )

    assert outcome == "cancelled"
    assert err == "Download cancelled by user"


def test_download_one_file_passes_through_extra_curl_args(monkeypatch, tmp_path):
    target = tmp_path / "model.gguf"
    status_path = tmp_path / "status.json"
    seen_cmds = []

    def fake_popen(cmd, **kwargs):
        seen_cmds.append(cmd)
        out_index = cmd.index("-o") + 1
        return FakeProc(returncode=0, write_bytes=b"data", tmp_path=type(target)(cmd[out_index]))

    monkeypatch.setattr(agent.subprocess, "Popen", fake_popen)

    agent._download_one_file(
        "https://example.com/model.gguf", target, status_path, "model.gguf",
        extra_curl_args=["-H", "Authorization: Bearer secret-token"],
    )

    assert "-H" in seen_cmds[0]
    assert "Authorization: Bearer secret-token" in seen_cmds[0]


def test_verify_sha256_or_fail_passes_on_match(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_bytes(b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    status_path = tmp_path / "status.json"

    assert agent._verify_sha256_or_fail(target, expected, status_path, "model.gguf") is True
    assert target.exists()


def test_verify_sha256_or_fail_deletes_file_on_mismatch(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_bytes(b"hello world")
    status_path = tmp_path / "status.json"

    assert agent._verify_sha256_or_fail(target, "0" * 64, status_path, "model.gguf") is False
    assert not target.exists()


def test_verify_sha256_or_fail_skips_when_no_checksum_provided(tmp_path):
    target = tmp_path / "model.gguf"
    target.write_bytes(b"hello world")
    status_path = tmp_path / "status.json"

    assert agent._verify_sha256_or_fail(target, "", status_path, "model.gguf") is True
    assert target.exists()


def test_write_hf_pull_manifest_records_every_file_with_its_own_target(monkeypatch, tmp_path):
    monkeypatch.setattr(agent, "INSTALL_DIR", tmp_path)
    diffusion_dir = tmp_path / "data" / "comfyui" / "ComfyUI" / "models" / "diffusion_models"
    vae_dir = tmp_path / "data" / "comfyui" / "ComfyUI" / "models" / "vae"
    diffusion_dir.mkdir(parents=True)
    vae_dir.mkdir(parents=True)
    file_plan = [
        {
            "filename": "flux1-schnell.safetensors",
            "repo_path": "flux1-schnell.safetensors",
            "target": "comfyui:diffusion_models",
            "final_target": diffusion_dir / "flux1-schnell.safetensors",
        },
        {
            "filename": "ae.safetensors",
            "repo_path": "vae/ae.safetensors",
            "target": "comfyui:vae",
            "final_target": vae_dir / "ae.safetensors",
        },
    ]

    agent._write_hf_pull_manifest("black-forest-labs/FLUX.1-schnell", "main", file_plan)

    manifest_dir = tmp_path / "data" / "hf-pulls"
    manifests = list(manifest_dir.glob("*.json"))
    assert len(manifests) == 1
    data = agent.json.loads(manifests[0].read_text(encoding="utf-8"))
    assert data["repo_id"] == "black-forest-labs/FLUX.1-schnell"
    assert data["revision"] == "main"
    assert data["files"] == [
        {
            "filename": "flux1-schnell.safetensors",
            "repo_path": "flux1-schnell.safetensors",
            "target": "comfyui:diffusion_models",
            "path": "data/comfyui/ComfyUI/models/diffusion_models/flux1-schnell.safetensors",
        },
        {
            "filename": "ae.safetensors",
            "repo_path": "vae/ae.safetensors",
            "target": "comfyui:vae",
            "path": "data/comfyui/ComfyUI/models/vae/ae.safetensors",
        },
    ]


@pytest.mark.parametrize("repo_path,expected", [
    ("model.gguf", True),
    ("vae/diffusion_pytorch_model.safetensors", True),
    ("text_encoder/model-00001-of-00002.safetensors", True),
    ("../outside.safetensors", False),
    ("vae/../../etc/passwd", False),
    ("/absolute.gguf", False),
    ("vae//double.safetensors", False),
    ("vae/./dot.safetensors", False),
    ("back\\slash.gguf", False),
    ("", False),
    ("a" * 513, False),
])
def test_valid_hf_repo_path(repo_path, expected):
    assert agent._valid_hf_repo_path(repo_path) is expected


def test_comfyui_subdirs_match_known_live_layout():
    # Regression guard: keep this in sync with the dashboard-api mirror
    # (routers/models.py) and the real data/comfyui/ComfyUI/models/ tree.
    expected = {
        "audio_encoders", "checkpoints", "clip", "clip_vision", "configs",
        "controlnet", "diffusers", "diffusion_models", "embeddings", "gligen",
        "hypernetworks", "latent_upscale_models", "loras", "model_patches",
        "photomaker", "style_models", "text_encoders", "unet", "upscale_models",
        "vae", "vae_approx",
    }
    assert agent._COMFYUI_MODEL_SUBDIRS == expected
