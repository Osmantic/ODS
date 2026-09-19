#!/usr/bin/env python3
"""Regression test: gpu module env reader respects dotenv override precedence and export prefix."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "extensions" / "services" / "dashboard-api"))

import gpu  # noqa: E402


def test_gpu_read_env_var_precedence_and_export():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        env_file = tmp_path / ".env"

        # Baseline: initial assignment overridden by later assignment; second var uses export prefix
        env_file.write_text(
            """
# Default configuration
GPU_BACKEND=cpu
# Custom override appended by installer or operator
GPU_BACKEND=nvidia
export GPU_ACCELERATOR=cuda
UNTOUCHED_VAR=kept
# Non-ASCII UTF-8 comment: 🚀
""",
            encoding="utf-8",
        )

        old_dir = os.environ.get("ODS_INSTALL_DIR")
        os.environ["ODS_INSTALL_DIR"] = str(tmp_path)
        try:
            # 1. Precedence: final assignment must override initial assignment
            found, backend = gpu._read_env_var_from_file_state("GPU_BACKEND")
            assert found is True
            assert backend == "nvidia", f"Expected 'nvidia', got {backend!r}"

            # 2. Export prefix: must parse key assigned with export prefix
            found_acc, acc = gpu._read_env_var_from_file_state("GPU_ACCELERATOR")
            assert found_acc is True
            assert acc == "cuda", f"Expected 'cuda', got {acc!r}"

            # 3. Missing key falls back cleanly to found=False
            found_missing, missing = gpu._read_env_var_from_file_state("NON_EXISTENT")
            assert found_missing is False
            assert missing == ""
        finally:
            if old_dir is None:
                os.environ.pop("ODS_INSTALL_DIR", None)
            else:
                os.environ["ODS_INSTALL_DIR"] = old_dir


if __name__ == "__main__":
    test_gpu_read_env_var_precedence_and_export()
    print("test_gpu_env_precedence: OK")
