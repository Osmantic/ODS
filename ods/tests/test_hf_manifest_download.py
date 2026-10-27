"""scripts/download-hf-manifest.py against a local stand-in for Hugging Face.

No network: a ThreadingHTTPServer on 127.0.0.1 serves /<repo>/resolve/<rev>/<path>
with Range support and can redirect, drop a connection mid-file, ignore Range
or serve wrong bytes, so resume, verification and the disk check all run here.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import threading
from collections import namedtuple
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "download-hf-manifest.py"
REPO = "nvidia/Test-Model-NVFP4"
REVISION = "0123456789abcdef0123456789abcdef01234567"
MODEL_DIR = "nvidia--Test-Model-NVFP4@01234567"
WEIGHTS = bytes(range(256)) * 1200 + b"tail"
FILES = {
    "config.json": b'{"architectures": ["TestForCausalLM"]}\n',
    "model-00001-of-00001.safetensors": WEIGHTS,
    "nested/tokenizer.json": b'{"version": "1.0"}' * 50,
}
LFS = {"model-00001-of-00001.safetensors", "nested/tokenizer.json"}
Usage = namedtuple("Usage", "total used free")


@pytest.fixture(autouse=True)
def roomy_volume(monkeypatch):
    """The disk check must not depend on the machine running the tests."""
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(10 * 1000 ** 4, 0, 9 * 1000 ** 4))


def _helper():
    spec = importlib.util.spec_from_file_location("ods_download_hf_manifest", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git_blob_sha1(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


def manifest(files: dict[str, bytes]) -> list[dict]:
    return [
        {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        if path in LFS else
        {"path": path, "size": len(content), "git_blob_sha1": git_blob_sha1(content)}
        for path, content in files.items()
    ]


def write_catalog(path: Path, files: dict[str, bytes], **overrides) -> Path:
    entry = {
        "id": "test-model-nvfp4-vllm", "name": "Test Model NVFP4 (vLLM)", "runtime": "vllm",
        "hf_repo": REPO, "revision": REVISION, "files": manifest(files),
        "served_model_name": "Test-Model-NVFP4", "llm_model_name": "test-model-nvfp4",
        "context_length": 8192, "min_gpu_mib": 1024, "compute_capability": ["10.0"],
        "reasoning": "off", "tool_call_parser": "hermes", "reasoning_parser": "qwen3",
        "vllm_args": ["--max-model-len", "8192", "--enable-auto-tool-choice",
                      "--tool-call-parser", "hermes", "--reasoning-parser", "qwen3"],
        "selection": {"discrete": 0, "unified": 0, "cpu": 0, "vllm": 1},
        **overrides,
    }
    gguf = {"id": "tiny-q4", "name": "Tiny", "gguf_file": "tiny.gguf", "gguf_url": "https://example.invalid/t.gguf"}
    path.write_text(json.dumps({"version": 2, "models": [gguf, entry]}), encoding="utf-8")
    return path


class FakeHub:
    """Serves FILES; switches make it misbehave the way a real download can."""

    def __init__(self, files: dict[str, bytes]):
        self.files = dict(files)
        self.requests: list[tuple[str, str | None]] = []
        self.drop_after: dict[str, int] = {}
        self.ignore_range: set[str] = set()
        self.redirect: set[str] = set()
        self.status: dict[str, int] = {}
        hub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_GET(self):
                hub.handle(self)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handle(self, request: BaseHTTPRequestHandler) -> None:
        self.requests.append((request.path, request.headers.get("Range")))
        prefix = f"/{REPO}/resolve/{REVISION}/"
        if request.path.startswith(prefix):
            path = request.path[len(prefix):]
            if path in self.redirect:
                request.send_response(302)
                request.send_header("Location", f"/cdn/{path}?signature=abc")
                request.send_header("Content-Length", "0")
                request.end_headers()
                return
        elif request.path.startswith("/cdn/"):
            path = request.path[len("/cdn/"):].split("?", 1)[0]
        else:
            path = ""
        if path in self.status or path not in self.files:
            request.send_response(self.status.get(path, 404))
            request.send_header("Content-Length", "0")
            request.end_headers()
            return
        self.send_file(request, path)

    def send_file(self, request: BaseHTTPRequestHandler, path: str) -> None:
        body = self.files[path]
        start = 0
        match = re.fullmatch(r"bytes=([0-9]+)-", request.headers.get("Range") or "")
        if match and path not in self.ignore_range:
            start = int(match.group(1))
            request.send_response(206)
            request.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        else:
            request.send_response(200)
        request.send_header("Content-Length", str(len(body) - start))
        request.end_headers()
        cut = self.drop_after.pop(path, None)
        if cut is not None:
            request.wfile.write(body[start:start + cut])
            request.wfile.flush()
            request.close_connection = True
            return
        request.wfile.write(body[start:])

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def gets(self, path: str) -> list[str | None]:
        return [rng for url, rng in self.requests if url.split("?", 1)[0].endswith("/" + path)]


@pytest.fixture
def hub():
    server = FakeHub(FILES)
    yield server
    server.close()


def run(tmp_path: Path, hub: FakeHub, catalog: Path | None = None, model_id: str = "test-model-nvfp4-vllm"):
    catalog = catalog or write_catalog(tmp_path / "model-library.json", FILES)
    return _helper().main([
        "--catalog", str(catalog), "--model-id", model_id,
        "--models-dir", str(tmp_path / "models"), "--base-url", hub.url,
    ])


def model_files(tmp_path: Path) -> dict[str, bytes]:
    root = tmp_path / "models" / MODEL_DIR
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_git_blob_sha1_matches_git():
    helper = _helper()
    for content, expected in ((b"hello\n", "ce013625030ba8dba906f756967f9e9ca394464a"),
                              (b"", "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")):
        digest = helper.new_digest({"path": "x", "size": len(content), "git_blob_sha1": expected})
        digest.update(content)
        assert digest.hexdigest() == expected


def test_downloads_and_verifies_every_file(tmp_path, hub, capsys):
    hub.redirect.add("model-00001-of-00001.safetensors")

    assert run(tmp_path, hub) == 0

    assert model_files(tmp_path) == FILES
    out = capsys.readouterr().out
    assert "progress: 100%" in out
    assert f"done: 3 files verified in {tmp_path / 'models' / MODEL_DIR}" in out
    # The redirect target (a CDN in real life) served the weights.
    assert any(url.startswith("/cdn/model-00001") for url, _ in hub.requests)


def test_interrupted_file_resumes_with_a_range_request(tmp_path, hub, capsys):
    hub.drop_after["model-00001-of-00001.safetensors"] = 100_000
    hub.redirect.add("model-00001-of-00001.safetensors")

    assert run(tmp_path, hub) == 1
    root = tmp_path / "models" / MODEL_DIR
    partial = root / "model-00001-of-00001.safetensors.partial"
    assert partial.stat().st_size == 100_000
    assert not (root / "model-00001-of-00001.safetensors").exists()
    assert "run this again to resume" in capsys.readouterr().err

    assert run(tmp_path, hub) == 0
    assert model_files(tmp_path) == FILES
    # The Range header survived the redirect to the CDN.
    assert hub.gets("model-00001-of-00001.safetensors")[-1] == "bytes=100000-"
    # Files verified on the first run were not fetched again.
    assert len(hub.gets("config.json")) == 1


def test_server_that_ignores_range_restarts_the_file(tmp_path, hub):
    root = tmp_path / "models" / MODEL_DIR
    root.mkdir(parents=True)
    (root / "model-00001-of-00001.safetensors.partial").write_bytes(WEIGHTS[:5000])
    hub.ignore_range.add("model-00001-of-00001.safetensors")

    assert run(tmp_path, hub) == 0

    assert model_files(tmp_path) == FILES
    assert hub.gets("model-00001-of-00001.safetensors") == ["bytes=5000-"]


def test_wrong_bytes_are_deleted_and_never_take_the_final_name(tmp_path, hub, capsys):
    hub.files["config.json"] = b'{"architectures": ["EvilForCausalLM"]}\n'[: len(FILES["config.json"])]
    assert len(hub.files["config.json"]) == len(FILES["config.json"])

    assert run(tmp_path, hub) == 3

    root = tmp_path / "models" / MODEL_DIR
    assert not (root / "config.json").exists()
    assert not (root / "config.json.partial").exists()
    assert "config.json does not match the catalog (git blob sha1" in capsys.readouterr().err


def test_verified_files_in_place_are_not_downloaded_again(tmp_path, hub):
    assert run(tmp_path, hub) == 0
    hub.requests.clear()

    assert run(tmp_path, hub) == 0

    assert hub.requests == []
    assert model_files(tmp_path) == FILES


def test_a_changed_file_in_place_is_replaced(tmp_path, hub, capsys):
    assert run(tmp_path, hub) == 0
    damaged = tmp_path / "models" / MODEL_DIR / "nested" / "tokenizer.json"
    damaged.write_bytes(b"x" * len(FILES["nested/tokenizer.json"]))
    capsys.readouterr()

    assert run(tmp_path, hub) == 0

    assert model_files(tmp_path) == FILES
    assert "replacing nested/tokenizer.json: it does not match the catalog" in capsys.readouterr().out


def test_refuses_to_start_without_room_for_the_rest_plus_a_margin(tmp_path, hub, monkeypatch, capsys):
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(100 * 1024 ** 3, 0, 2 * 1024 ** 3))
    catalog = write_catalog(tmp_path / "model-library.json", FILES)

    assert run(tmp_path, hub, catalog, model_id="test-model-nvfp4") == 4

    assert hub.requests == []
    err = capsys.readouterr().err
    # 5% of a 100 GiB volume (more than the 2 GiB floor) stays free.
    assert "has 2.1 GB free, but this model needs 0.0 GB more plus a 5.4 GB safety margin" in err
    assert "discord.gg" in err


def test_a_resumed_download_only_needs_room_for_the_rest(tmp_path):
    helper = _helper()
    root = tmp_path / "models" / MODEL_DIR
    root.mkdir(parents=True)
    (root / "model-00001-of-00001.safetensors.partial").write_bytes(WEIGHTS[:300_000])
    (root / "config.json").write_bytes(FILES["config.json"])

    assert helper.bytes_to_download(root, manifest(FILES)) == (
        len(WEIGHTS) - 300_000 + len(FILES["nested/tokenizer.json"])
    )


def test_a_complete_model_needs_no_free_space(tmp_path, hub, monkeypatch):
    assert run(tmp_path, hub) == 0
    monkeypatch.setattr(shutil, "disk_usage", lambda path: Usage(100 * 1024 ** 3, 0, 0))

    assert run(tmp_path, hub) == 0


def test_http_errors_name_the_file_not_the_redirect(tmp_path, hub, capsys):
    hub.status["nested/tokenizer.json"] = 403

    assert run(tmp_path, hub) == 1

    err = capsys.readouterr().err
    assert f"HTTP 403 for {hub.url}/{REPO}/resolve/{REVISION}/nested/tokenizer.json" in err


@pytest.mark.parametrize("path", ["../escape.json", "/etc/passwd", "a//b.json", "a\\b.json", "x.partial"])
def test_unsafe_manifest_paths_are_refused_before_any_download(tmp_path, hub, path, capsys):
    files = {**FILES, path: b"{}"}
    catalog = write_catalog(tmp_path / "model-library.json", files)

    assert run(tmp_path, hub, catalog) == 1

    assert hub.requests == []
    assert not (tmp_path / "models").exists()
    assert "invalid vLLM catalog entry" in capsys.readouterr().err


def test_only_vllm_catalog_entries_download(tmp_path, hub, capsys):
    assert run(tmp_path, hub, model_id="tiny-q4") == 1
    assert "is a llama.cpp model" in capsys.readouterr().err
    assert run(tmp_path, hub, model_id="missing") == 1


def test_real_catalog_entries_resolve_to_their_model_folders():
    helper = _helper()
    catalog = ROOT / "config" / "model-library.json"
    for model_id, folder in (
        ("qwen3.8-flash-next-nvfp4-vllm", "nvidia--Qwen3.8-Flash-Next-NVFP4@fc694b54"),
        ("glm-5.3-flash-nvfp4-vllm", "nvidia--GLM-5.3-Flash-NVFP4@da920bb0"),
    ):
        entry = helper.find_entry(catalog, model_id)
        assert helper.model_dir_name(entry) == folder
        assert helper.file_url(helper.DEFAULT_BASE_URL, entry, "config.json") == (
            f"https://huggingface.co/{entry['hf_repo']}/resolve/{entry['revision']}/config.json"
        )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
