from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


HELPER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "download-hf-artifact.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("download_hf_artifact", HELPER_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_single_segment_repo_url():
    helper = _load_helper()
    repo_id, revision, filename = helper.parse_huggingface_resolve_url(
        "https://huggingface.co/gpt2/resolve/main/model.safetensors"
    )
    assert repo_id == "gpt2"
    assert revision == "main"
    assert filename == "model.safetensors"


def test_parse_dataset_namespaced_url():
    helper = _load_helper()
    repo_id, revision, filename = helper.parse_huggingface_resolve_url(
        "https://hf.co/datasets/allenai/c4/resolve/main/en/c4-train.json.gz"
    )
    assert repo_id == "allenai/c4"
    assert revision == "main"
    assert filename == "en/c4-train.json.gz"


def test_parse_dataset_single_segment_url():
    helper = _load_helper()
    repo_id, revision, filename = helper.parse_huggingface_resolve_url(
        "https://huggingface.co/datasets/glue/resolve/v2.0/glue.py"
    )
    assert repo_id == "glue"
    assert revision == "v2.0"
    assert filename == "glue.py"


def test_parse_rejects_missing_filename_or_revision():
    helper = _load_helper()
    for bad_url in [
        "https://huggingface.co/gpt2/resolve",
        "https://huggingface.co/gpt2/resolve/main",
    ]:
        try:
            helper.parse_huggingface_resolve_url(bad_url)
            raise AssertionError(f"Expected ValueError for {bad_url}")
        except ValueError as exc:
            assert "not a Hugging Face /resolve/ artifact URL" in str(exc)


def test_cli_print_metadata_single_segment():
    cmd = [
        sys.executable,
        str(HELPER_PATH),
        "https://huggingface.co/bert-base-uncased/resolve/v1.0/pytorch_model.bin",
        "/tmp/unused",
        "--print-metadata",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    lines = proc.stdout.strip().splitlines()
    assert "repo_id=bert-base-uncased" in lines
    assert "revision=v1.0" in lines
    assert "filename=pytorch_model.bin" in lines


if __name__ == "__main__":
    test_parse_single_segment_repo_url()
    test_parse_dataset_namespaced_url()
    test_parse_dataset_single_segment_url()
    test_parse_rejects_missing_filename_or_revision()
    test_cli_print_metadata_single_segment()
    print("All Hugging Face resolve URL scope tests passed.")

