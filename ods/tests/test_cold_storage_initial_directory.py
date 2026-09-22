"""Archiving idle models must create cold storage directory if absent."""

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "llm-cold-storage.sh"


def test_archive_creates_cold_directory_and_moves_model(tmp_path):
    cache = tmp_path / "cache"
    name = "models--ODSOrg--idle-model"
    model = cache / name
    model.mkdir(parents=True)
    weights = model / "weights.bin"
    weights.write_text("tensor data")

    aged_atime = time.time() - 864000  # 10 days ago
    os.utime(weights, (aged_atime, aged_atime))

    cold = tmp_path / "cold_archive"
    assert not cold.exists()

    env = {
        **os.environ,
        "HF_CACHE": str(cache),
        "COLD_DIR": str(cold),
        "LOG_FILE": str(tmp_path / "logs" / "storage.log"),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), "--execute"],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "ARCHIVING: " in result.stdout
    assert "ARCHIVED: " in result.stdout

    assert cold.is_dir(), "Cold storage directory was not created"
    archived_model = cold / name
    assert archived_model.is_dir(), "Model was not moved to cold storage"
    assert (archived_model / "weights.bin").read_text() == "tensor data"

    # Verification: original cache path must now be a working symlink
    assert model.is_symlink(), "Cache model path is not a symlink"
    assert model.resolve() == archived_model.resolve()


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        test_archive_creates_cold_directory_and_moves_model(Path(temp_dir))
    print("[PASS] test_archive_creates_cold_directory_and_moves_model")
