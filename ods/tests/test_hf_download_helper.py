from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock, Timeout


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download-hf-artifact.py"
SNAPSHOT_HELPER_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "download-hf-snapshot.py"
)
COMMIT = "a" * 40
URL = f"https://huggingface.co/org/repo/resolve/{COMMIT}/nested/model.gguf"


def _stub_hub(monkeypatch, download, commit=COMMIT):
    monkeypatch.setitem(
        sys.modules, "huggingface_hub",
        SimpleNamespace(hf_hub_download=download,
                        get_hf_file_metadata=lambda url: SimpleNamespace(commit_hash=commit)),
    )


def _load_helper():
    spec = importlib.util.spec_from_file_location("download_hf_artifact", HELPER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_snapshot_helper():
    spec = importlib.util.spec_from_file_location(
        "download_hf_snapshot", SNAPSHOT_HELPER_PATH
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_huggingface_resolve_url_with_nested_filename():
    helper = _load_helper()

    repo_id, revision, filename = helper.parse_huggingface_resolve_url(
        "https://huggingface.co/unsloth/Llama-4-Scout-GGUF/resolve/main/"
        "Q4_K_M/model-00001-of-00002.gguf"
    )

    assert repo_id == "unsloth/Llama-4-Scout-GGUF"
    assert revision == "main"
    assert filename == "Q4_K_M/model-00001-of-00002.gguf"


def test_parse_huggingface_resolve_url_rejects_non_hf_url():
    helper = _load_helper()

    with pytest.raises(ValueError, match="not a Hugging Face URL"):
        helper.parse_huggingface_resolve_url("https://example.com/model.gguf")


@pytest.mark.parametrize("existing_partial", [False, True])
def test_download_artifact_moves_owned_staging_without_copying(
    monkeypatch, tmp_path, existing_partial
):
    helper = _load_helper()
    destination = tmp_path / "models" / "model.gguf.part"
    if existing_partial:
        destination.parent.mkdir()
        destination.write_bytes(b"existing curl partial")
    calls = []
    source_identity = []

    def fake_hf_hub_download(**kwargs):
        calls.append(kwargs)
        staging = Path(kwargs["local_dir"])
        assert staging.parent.parent.parent == destination.parent
        downloaded = staging / "nested" / "model.gguf"
        downloaded.parent.mkdir()
        downloaded.write_bytes(b"GGUF complete")
        source_identity.append(downloaded.stat().st_ino)
        (staging / ".cache").mkdir()
        (staging / ".cache" / "metadata").write_text("owned metadata")
        return str(downloaded)

    _stub_hub(monkeypatch, fake_hf_hub_download)
    result = helper.download_artifact(URL, destination)

    assert result == destination
    assert result.read_bytes() == b"GGUF complete"
    assert result.stat().st_ino == source_identity[0]
    assert calls == [{"repo_id": "org/repo", "filename": "nested/model.gguf",
                      "revision": COMMIT, "local_dir": calls[0]["local_dir"]}]
    stage = Path(calls[0]["local_dir"]).parent
    assert set(destination.parent.iterdir()) == {destination, stage.parent}
    assert (stage / "owner.json").is_file()
    assert {path.name for path in stage.iterdir()} <= {"owner.json", "download.lock"}


@pytest.mark.parametrize("failure", ["download", "empty", "replace"])
def test_download_artifact_preserves_partial_and_sdk_state_on_failure(
    monkeypatch, tmp_path, failure
):
    helper = _load_helper()
    destination = tmp_path / "model.gguf.part"
    destination.write_bytes(b"existing curl partial")
    unrelated = tmp_path / ".model.gguf.part.hf-unrelated"
    unrelated.mkdir()
    unrelated_file = unrelated / "keep"
    unrelated_file.write_text("another operation")
    stages = []

    def fake_hf_hub_download(**kwargs):
        staging = Path(kwargs["local_dir"])
        stages.append(staging)
        downloaded = staging / "model.gguf"
        downloaded.write_bytes(b"" if failure == "empty" else b"GGUF complete")
        if failure == "download":
            raise OSError("interrupted transfer")
        return str(downloaded)

    real_replace = Path.replace

    def fail_replace(source, target):
        if target == destination:
            raise OSError("replacement failed")
        return real_replace(source, target)

    _stub_hub(monkeypatch, fake_hf_hub_download)
    if failure == "replace":
        monkeypatch.setattr(Path, "replace", fail_replace)

    with pytest.raises((OSError, RuntimeError)):
        helper.download_artifact(
            f"https://huggingface.co/org/repo/resolve/{COMMIT}/model.gguf", destination
        )

    assert destination.read_bytes() == b"existing curl partial"
    assert unrelated_file.read_text() == "another operation"
    assert set(tmp_path.iterdir()) == {destination, unrelated, stages[0].parent.parent}
    assert stages[0].is_dir()
    assert (stages[0] / "model.gguf").exists() == (failure != "empty")


def test_retry_reuses_sdk_partial_and_cleans_only_after_publish(monkeypatch, tmp_path):
    helper = _load_helper()
    destination = tmp_path / "model.gguf.part"
    destination.write_bytes(b"curl partial")
    stages = []

    def download(**kwargs):
        payload = Path(kwargs["local_dir"])
        stages.append(payload)
        incomplete = payload / "sdk.incomplete"
        if not incomplete.exists():
            incomplete.write_bytes(b"GGUF first")
            raise OSError("interrupted")
        assert incomplete.read_bytes() == b"GGUF first"
        artifact = payload / "nested" / "model.gguf"
        artifact.parent.mkdir()
        artifact.write_bytes(incomplete.read_bytes() + b" resumed")
        return str(artifact)

    _stub_hub(monkeypatch, download)
    with pytest.raises(OSError, match="interrupted"):
        helper.download_artifact(URL, destination)
    assert destination.read_bytes() == b"curl partial"
    helper.download_artifact(URL, destination)
    assert stages[0] == stages[1]
    assert destination.read_bytes() == b"GGUF first resumed"
    assert not stages[0].exists()


def test_moving_revision_is_pinned_before_staging(monkeypatch, tmp_path):
    helper = _load_helper()
    seen = []

    def download(**kwargs):
        seen.append(kwargs)
        raise OSError("interrupted")

    _stub_hub(monkeypatch, download)
    with pytest.raises(OSError):
        helper.download_artifact(URL.replace(COMMIT, "main"), tmp_path / "model.part")
    assert seen[0]["revision"] == COMMIT
    marker = Path(seen[0]["local_dir"]).parent / "owner.json"
    assert json.loads(marker.read_text())["revision"] == COMMIT


def test_different_revision_does_not_reuse_old_stage(monkeypatch, tmp_path):
    helper = _load_helper()
    seen = []

    def download(**kwargs):
        stage = Path(kwargs["local_dir"])
        seen.append(stage)
        assert not (stage / "sdk.incomplete").exists()
        (stage / "sdk.incomplete").write_text(kwargs["revision"])
        raise OSError("interrupted")

    _stub_hub(monkeypatch, download)
    for commit in (COMMIT, "b" * 40):
        with pytest.raises(OSError):
            helper.download_artifact(URL.replace(COMMIT, commit), tmp_path / "model.part")
    assert seen[0] != seen[1]
    assert (seen[0] / "sdk.incomplete").read_text() == COMMIT


@pytest.mark.parametrize("unsafe", ["owner", "hardlink", "lock"])
def test_retry_rejects_unsafe_or_busy_stage(monkeypatch, tmp_path, unsafe):
    helper = _load_helper()
    destination = tmp_path / "model.part"
    seen = []

    def download(**kwargs):
        seen.append(Path(kwargs["local_dir"]).parent)
        raise OSError("interrupted")

    _stub_hub(monkeypatch, download)
    with pytest.raises(OSError):
        helper.download_artifact(URL, destination)
    stage = seen[0]
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"preserve")
    if unsafe == "owner":
        (stage / "owner.json").write_text("wrong identity")
    elif unsafe == "hardlink":
        os.link(unrelated, stage / "payload" / "foreign")

    if unsafe == "lock":
        with FileLock(str(stage.parent / "download.lock")):
            for commit in (COMMIT, "b" * 40):
                with pytest.raises(Timeout):
                    helper.download_artifact(URL.replace(COMMIT, commit), destination)
    else:
        with pytest.raises(RuntimeError):
            helper.download_artifact(URL, destination)
    assert len(seen) == 1
    assert unrelated.read_bytes() == b"preserve"


def test_retry_rejects_symlink_inside_staging(monkeypatch, tmp_path):
    helper = _load_helper()
    seen = []

    def download(**kwargs):
        seen.append(Path(kwargs["local_dir"]))
        raise OSError("interrupted")

    _stub_hub(monkeypatch, download)
    with pytest.raises(OSError):
        helper.download_artifact(URL, tmp_path / "model.part")
    unrelated = tmp_path / "unrelated"
    unrelated.write_bytes(b"preserve")
    try:
        (seen[0] / "foreign").symlink_to(unrelated)
    except OSError:
        pytest.skip("This host does not permit creating symlinks")
    with pytest.raises(RuntimeError, match="unsafe filesystem"):
        helper.download_artifact(URL, tmp_path / "model.part")
    assert len(seen) == 1
    assert unrelated.read_bytes() == b"preserve"


def test_unresolved_moving_revision_does_not_create_staging(monkeypatch, tmp_path):
    _stub_hub(monkeypatch, lambda **kwargs: pytest.fail("must not download"), commit=None)
    with pytest.raises(RuntimeError, match="immutable commit identity"):
        _load_helper().download_artifact(URL.replace(COMMIT, "main"), tmp_path / "model.part")
    assert list(tmp_path.iterdir()) == []


def test_download_cannot_publish_an_unrelated_file(monkeypatch, tmp_path):
    helper = _load_helper()
    unrelated = tmp_path / "shared-cache.gguf"
    unrelated.write_bytes(b"GGUF unrelated")
    destination = tmp_path / "model.part"
    destination.write_bytes(b"curl partial")
    _stub_hub(monkeypatch, lambda **kwargs: str(unrelated))
    with pytest.raises(RuntimeError, match="outside owned staging"):
        helper.download_artifact(URL, destination)
    assert unrelated.read_bytes() == b"GGUF unrelated"
    assert destination.read_bytes() == b"curl partial"


def test_published_download_succeeds_when_real_filesystem_cleanup_fails(
    monkeypatch, tmp_path, capsys
):
    if os.name != "nt" and os.geteuid() == 0:
        pytest.skip("Root bypasses the filesystem permission denial")
    helper = _load_helper()
    destination = tmp_path / "model.part"
    destination.write_bytes(b"curl partial")
    held = []

    def download(**kwargs):
        payload = Path(kwargs["local_dir"])
        artifact = payload / "nested" / "model.gguf"
        artifact.parent.mkdir()
        artifact.write_bytes(b"GGUF complete")
        metadata = payload / ".cache" / "metadata"
        metadata.parent.mkdir()
        metadata.write_bytes(b"owned SDK metadata")
        if os.name == "nt":
            import ctypes

            create = ctypes.windll.kernel32.CreateFileW
            create.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong,
                               ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
            create.restype = ctypes.c_void_p
            # A real open handle without FILE_SHARE_DELETE blocks rmtree on Windows.
            handle = create("\\\\?\\" + str(metadata.resolve()), 0x80000000, 3, None, 3, 128, None)
            assert handle not in (None, ctypes.c_void_p(-1).value)
            close = ctypes.windll.kernel32.CloseHandle
            close.argtypes = [ctypes.c_void_p]
            held.append((metadata, lambda: close(handle)))
        else:
            # The model can be renamed while this separate metadata directory
            # remains readable but disallows unlinking its file.
            metadata.parent.chmod(0o500)
            held.append((metadata, lambda: metadata.parent.chmod(0o700)))
        return str(artifact)

    _stub_hub(monkeypatch, download)
    try:
        result = helper.download_artifact(URL, destination)
        assert result == destination
        assert result.read_bytes() == b"GGUF complete"
        assert held[0][0].read_bytes() == b"owned SDK metadata"
        assert "Artifact published; SDK staging cleanup pending" in capsys.readouterr().err
    finally:
        for _, release in held:
            release()


@pytest.mark.parametrize("filename", ["../escape.gguf", "sub/../escape.gguf", "C%3Aescape.gguf", "sub%5Cescape.gguf"])
def test_staging_rejects_unsafe_filename(tmp_path, filename):
    with pytest.raises(ValueError, match="safe relative path"):
        _load_helper().download_artifact(
            f"https://huggingface.co/org/repo/resolve/{COMMIT}/{filename}", tmp_path / "model.part"
        )
    assert list(tmp_path.iterdir()) == []


def test_download_snapshot_passes_cache_revision_and_patterns(monkeypatch, tmp_path):
    helper = _load_snapshot_helper()
    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        snapshot = tmp_path / "models--BAAI--bge-base-en-v1.5" / "snapshots" / "abc"
        snapshot.mkdir(parents=True)
        return str(snapshot)

    monkeypatch.setitem(
        sys.modules,
        "huggingface_hub",
        SimpleNamespace(snapshot_download=fake_snapshot_download),
    )

    result = helper.download_snapshot(
        "BAAI/bge-base-en-v1.5",
        tmp_path / "cache",
        revision="main",
        allow_patterns=["onnx/model.onnx"],
    )

    assert result.name == "abc"
    assert calls == [
        {
            "repo_id": "BAAI/bge-base-en-v1.5",
            "cache_dir": str(tmp_path / "cache"),
            "revision": "main",
            "allow_patterns": ["onnx/model.onnx"],
        }
    ]
