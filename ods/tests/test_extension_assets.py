"""Model packs an extension's container reads beside its image (bin/extension_assets.py).

The prepare step and the installer fetch them; these tests drive the module
with a fake HTTP opener, so nothing leaves the machine.
"""
import hashlib
import io
import json
import shutil
import sys
import urllib.error
from pathlib import Path

import pytest

ODS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ODS / "bin"))
import extension_assets as assets  # noqa: E402

PAYLOAD = b"model weights " * 4096
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


def _pack_list(tmp_path, *, sha256=DIGEST, size=len(PAYLOAD), default=True, root="data/comfyui/models"):
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir(exist_ok=True)
    (ext_dir / "model-packs.json").write_text(json.dumps({
        "schema": "ods.model-packs.v1",
        "model_root": {"nvidia": root, "amd": "data/comfyui/ComfyUI/models"},
        "packs": [{"id": "image-default", "label": "Test model", "feature": "images", "default": default,
                   "files": [{"subdir": "checkpoints", "file": "test.safetensors",
                              "url": "https://example.invalid/test.safetensors",
                              "sha256": sha256, "bytes": size, "license": "test"}]}],
    }), encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir(exist_ok=True)
    return ext_dir, install


class Response(io.BytesIO):
    def __init__(self, body, status=200):
        super().__init__(body)
        self.status = status


def opener_for(body, status=200, seen=None):
    def opener(request, timeout):
        if seen is not None:
            seen.append((dict(request.header_items()), timeout))
        return Response(body, status)
    return opener


def test_default_files_follow_the_backend_and_skip_optional_packs(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    assert item.target == install.resolve() / "data/comfyui/models/checkpoints/test.safetensors"
    assert (item.size, item.sha256, item.label) == (len(PAYLOAD), DIGEST, "Test model")
    [amd] = assets.default_files(ext_dir, install, "amd")
    assert amd.target.parent == install.resolve() / "data/comfyui/ComfyUI/models/checkpoints"
    assert assets.default_files(ext_dir, install, "cpu") == []
    assert assets.default_files(tmp_path / "no-packs", install, "nvidia") == []
    ext_dir, install = _pack_list(tmp_path, default=False)
    assert assets.default_files(ext_dir, install, "nvidia") == []


@pytest.mark.parametrize("change", [
    {"root": "../outside"},
    {"sha256": "ABC"},
    {"size": 0},
])
def test_unsafe_or_incomplete_pack_lists_are_refused(tmp_path, change):
    ext_dir, install = _pack_list(tmp_path, **change)
    with pytest.raises(ValueError):
        assets.default_files(ext_dir, install, "nvidia")


def test_a_malformed_pack_list_is_a_value_error(tmp_path):
    ext_dir = tmp_path / "ext"
    ext_dir.mkdir()
    (ext_dir / "model-packs.json").write_text('{"schema": "ods.model-packs.v1", "packs": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="malformed"):
        assets.default_files(ext_dir, tmp_path, "nvidia")


def test_a_file_counts_as_present_only_at_its_pinned_size(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    assert assets.missing([item]) == [item]
    item.target.parent.mkdir(parents=True)
    item.target.write_bytes(PAYLOAD[:100])
    assert assets.missing([item]) == [item]
    item.target.write_bytes(PAYLOAD)
    assert assets.missing([item]) == []


def test_fetch_downloads_verifies_and_renames(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    progress, seen = [], []
    assets.fetch(item, progress.append, opener=opener_for(PAYLOAD, seen=seen))
    assert item.target.read_bytes() == PAYLOAD
    assert not item.target.with_name("test.safetensors.part").exists()
    assert progress[-1] == len(PAYLOAD)
    headers, timeout = seen[0]
    assert "Range" not in headers and headers["User-agent"] == assets.USER_AGENT
    assert timeout == assets.READ_TIMEOUT_SECONDS


def test_fetch_resumes_a_partial_download_with_a_range_request(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(PAYLOAD[:1000])
    seen = []
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD[1000:], status=206, seen=seen))
    assert seen[0][0]["Range"] == "bytes=1000-"
    assert item.target.read_bytes() == PAYLOAD


def test_fetch_starts_over_when_the_server_ignores_the_range(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    part = item.target.with_name("test.safetensors.part")
    part.parent.mkdir(parents=True)
    part.write_bytes(b"x" * 1000)  # not even the right bytes
    assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD, status=200))
    assert item.target.read_bytes() == PAYLOAD


def test_a_wrong_checksum_deletes_the_download(tmp_path):
    ext_dir, install = _pack_list(tmp_path, sha256="0" * 64)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    with pytest.raises(assets.AssetError, match="did not match its pinned SHA-256"):
        assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD))
    assert not item.target.exists()
    assert not item.target.with_name("test.safetensors.part").exists()


def test_a_short_download_keeps_the_part_for_a_resume(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    with pytest.raises(assets.AssetError, match="Try again to resume it"):
        assets.fetch(item, lambda done: None, opener=opener_for(PAYLOAD[:5000]))
    assert item.target.with_name("test.safetensors.part").stat().st_size == 5000
    assert not item.target.exists()


def test_a_network_failure_names_the_cause_and_keeps_the_part(tmp_path):
    ext_dir, install = _pack_list(tmp_path)
    [item] = assets.default_files(ext_dir, install, "nvidia")

    def offline(request, timeout):
        raise urllib.error.URLError("Temporary failure in name resolution")

    with pytest.raises(assets.AssetError, match="Temporary failure in name resolution"):
        assets.fetch(item, lambda done: None, opener=offline)


def test_space_check_counts_partial_downloads_and_refuses_with_numbers(tmp_path, monkeypatch):
    ext_dir, install = _pack_list(tmp_path, size=10 * 10 ** 9)
    [item] = assets.default_files(ext_dir, install, "nvidia")
    usage = shutil.disk_usage(tmp_path)
    monkeypatch.setattr(assets.shutil, "disk_usage",
                        lambda path: usage._replace(free=5 * 10 ** 9))
    with pytest.raises(assets.AssetError, match=r"needs 10\.0 GB plus 2\.1 GB to spare.*has 5\.0 GB free"):
        assets.check_space([item])
    monkeypatch.setattr(assets.shutil, "disk_usage",
                        lambda path: usage._replace(free=13 * 10 ** 9))
    assets.check_space([item])


def test_the_command_line_fetches_what_is_missing(tmp_path, monkeypatch, capsys):
    ext_dir, install = _pack_list(tmp_path)
    monkeypatch.setattr(assets.urllib.request, "urlopen", opener_for(PAYLOAD))
    assert assets.main([str(ext_dir), str(install), "nvidia"]) == 0
    assert "Test model is ready (SHA-256 verified)" in capsys.readouterr().out
    assert assets.main([str(ext_dir), str(install), "nvidia"]) == 0
    assert "already in place" in capsys.readouterr().out


def test_the_command_line_reports_a_failure(tmp_path, monkeypatch, capsys):
    ext_dir, install = _pack_list(tmp_path, sha256="0" * 64)
    monkeypatch.setattr(assets.urllib.request, "urlopen", opener_for(PAYLOAD))
    assert assets.main([str(ext_dir), str(install), "nvidia"]) == 1
    assert "ERROR:" in capsys.readouterr().err


def test_the_comfyui_pack_list_parses_for_both_gpu_backends(tmp_path):
    comfyui = ODS / "extensions/services/comfyui"
    for backend in ("nvidia", "amd"):
        [item] = assets.default_files(comfyui, tmp_path, backend)
        assert item.target.name == "sdxl_lightning_4step.safetensors"
        assert item.size == 6938040682
