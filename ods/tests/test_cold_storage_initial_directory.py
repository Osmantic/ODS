"""Cold storage archive must create target directory before moving idle models."""

import os
import subprocess
import time
from pathlib import Path


def test_archive_creates_cold_dir_and_restores(tmp_path):
    script_path = Path(__file__).resolve().parents[1] / "scripts/llm-cold-storage.sh"
    hf_cache = tmp_path / "cache/hub"
    cold_dir = tmp_path / "cold_archive_missing_dir"
    log_file = tmp_path / "test.log"

    model_dir = hf_cache / "models--testorg--testmodel"
    model_dir.mkdir(parents=True)
    sample_file = model_dir / "model.bin"
    sample_file.write_text("weights")

    old_time = time.time() - (10 * 86400)
    os.utime(sample_file, (old_time, old_time))
    os.utime(model_dir, (old_time, old_time))

    assert not cold_dir.exists()

    env = dict(
        os.environ,
        HF_CACHE=str(hf_cache),
        COLD_DIR=str(cold_dir),
        LOG_FILE=str(log_file),
    )

    result = subprocess.run(
        ["bash", str(script_path), "--execute"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"stdout: {result.stdout}, stderr: {result.stderr}"
    assert cold_dir.is_dir()
    archived_model = cold_dir / "models--testorg--testmodel"
    assert archived_model.is_dir()
    assert (archived_model / "model.bin").read_text() == "weights"
    assert model_dir.is_symlink()
    assert model_dir.resolve() == archived_model.resolve()

    # Now test restore when model is in cold storage
    restore_result = subprocess.run(
        ["bash", str(script_path), "--restore", "models--testorg--testmodel"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert restore_result.returncode == 0, f"stdout: {restore_result.stdout}, stderr: {restore_result.stderr}"
    assert not model_dir.is_symlink()
    assert model_dir.is_dir()
    assert (model_dir / "model.bin").read_text() == "weights"


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        test_archive_creates_cold_dir_and_restores(Path(td))
        print("test_cold_storage_initial_directory passed")

