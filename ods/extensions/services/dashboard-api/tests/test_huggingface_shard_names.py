"""Public Hub imports must produce names consumable by llama.cpp's split loader."""

import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess

import pytest

from routers import models


def import_shards(client, monkeypatch, tmp_path, *, prefix="quant/model-Q4_K_M", repo="org/repo", revision="c" * 40):
    names = [f"{prefix}-{index:05d}-of-00002.gguf" for index in (1, 2)]
    payload = {
        "id": repo, "sha": revision, "pipeline_tag": "text-generation",
        "siblings": [{"rfilename": name, "lfs": {
            "size": len(tiny_shard(index)), "sha256": hashlib.sha256(tiny_shard(index)).hexdigest(),
        }} for index, name in enumerate(names)],
    }

    async def hub(*_args, **_kwargs):
        return payload, {}

    calls = []
    monkeypatch.setattr(models, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(models, "_hf_get_json", hub)
    monkeypatch.setattr(models, "_bootstrap_upgrade_download_conflict", lambda: None)
    monkeypatch.setattr(models, "_call_agent_model", lambda path, body: calls.append((path, body)) or {"status": "started"})
    details = client.get(f"/api/models/huggingface/repositories/{repo}", headers=client.auth_headers)
    assert details.status_code == 200, details.text
    artifacts = details.json()["artifacts"]
    assert len(artifacts) == 1 and artifacts[0]["split"]
    response = client.post("/api/models/huggingface/import", headers=client.auth_headers,
                           json={"repoId": repo, "artifactId": artifacts[0]["id"]})
    assert response.status_code == 200, response.text
    records = json.loads((tmp_path / "model-imports.json").read_text())["models"]
    record = next(item for item in records if item["id"] == response.json()["modelId"])
    assert calls == [("/v1/model/download", {
        "gguf_file": record["gguf_file"], "gguf_url": record["gguf_url"],
        "gguf_sha256": record["gguf_sha256"], "gguf_parts": record["gguf_parts"],
    })]
    for index, (name, part) in enumerate(zip(names, record["gguf_parts"])):
        assert part["source_file"] == name
        assert f"/resolve/{revision}/" in part["url"]
        assert part["size_bytes"] == len(tiny_shard(index))
        assert part["sha256"] == hashlib.sha256(tiny_shard(index)).hexdigest()
    return record


@pytest.mark.parametrize("prefix", ["model-Q4_K_M", "quant/model-Q4_K_M", "quant/model with spaces", "quant/" + "long-model-" * 20])
def test_imported_parts_share_a_loader_compatible_prefix(test_client, monkeypatch, tmp_path, prefix):
    record = import_shards(test_client, monkeypatch, tmp_path, prefix=prefix)
    names = [part["file"] for part in record["gguf_parts"]]
    assert record["gguf_file"] == names[0]
    first = re.fullmatch(r"(.+)-00001-of-00002\.gguf", names[0])
    assert first, names
    assert names[1] == f"{first[1]}-00002-of-00002.gguf"
    assert all(Path(name).name == name and len(name) <= 220 for name in names)


def test_split_imports_remain_distinct_across_repositories_revisions_and_directories(test_client, monkeypatch, tmp_path):
    groups = []
    for options in ({}, {"repo": "other/repo"}, {"revision": "a" * 40}, {"prefix": "other/model-Q4_K_M"}):
        record = import_shards(test_client, monkeypatch, tmp_path, **options)
        groups.append({part["file"] for part in record["gguf_parts"]})
    assert len(set.union(*groups)) == 8


def test_single_file_import_names_remain_stable():
    identity = "org/repo\n" + "c" * 40 + "\nquant/model-Q4_K_M.gguf"
    digest = hashlib.sha256(identity.encode()).hexdigest()[:8]
    assert models._hf_local_filename("org/repo", "quant/model-Q4_K_M.gguf", "c" * 40) == f"hf-org-repo-model-Q4_K_M-{digest}.gguf"


def tiny_shard(index):
    """GGUF v3 containing one F32 tensor and the standard split metadata."""
    def string(value):
        raw = value.encode()
        return struct.pack("<Q", len(raw)) + raw

    data = b"GGUF" + struct.pack("<IQQ", 3, 1, 3)
    for key, value in (("split.no", index), ("split.count", 2)):
        data += string(key) + struct.pack("<IH", 2, value)  # GGUF UINT16
    data += string("split.tensors.count") + struct.pack("<Ii", 5, 2)  # GGUF INT32
    data += string(f"tensor_{index}") + struct.pack("<IQIQ", 1, 1, 0, 0)
    data += bytes((-len(data)) % 32)
    return data + struct.pack("<f", index + 1.25) + bytes(28)


@pytest.mark.skipif(not os.environ.get("ODS_TEST_GGUF_SPLIT"), reason="Set ODS_TEST_GGUF_SPLIT to a llama-gguf-split executable")
def test_real_gguf_reader_consumes_both_imported_shards(test_client, monkeypatch, tmp_path):
    record = import_shards(test_client, monkeypatch, tmp_path)
    binary = os.environ["ODS_TEST_GGUF_SPLIT"]
    # Prove the exact fixture bytes are valid under the upstream tool before
    # checking the local names emitted by the import endpoint.
    for index, part in enumerate(record["gguf_parts"]):
        raw = tiny_shard(index)
        (tmp_path / f"control-{index + 1:05d}-of-00002.gguf").write_bytes(raw)
        (tmp_path / part["file"]).write_bytes(raw)
    for first, output in (("control-00001-of-00002.gguf", "control.gguf"), (record["gguf_file"], "imported.gguf")):
        result = subprocess.run([binary, "--merge", str(tmp_path / first), str(tmp_path / output)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        merged = (tmp_path / output).read_bytes()
        assert struct.unpack_from("<Q", merged, 8)[0] == 2
        assert struct.unpack_from("<f", merged, len(merged) - 64)[0] == 1.25
        assert struct.unpack_from("<f", merged, len(merged) - 32)[0] == 2.25
    assert (tmp_path / "control.gguf").read_bytes() == (tmp_path / "imported.gguf").read_bytes()
