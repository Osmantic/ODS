#!/usr/bin/env python3
"""Regression test for load-backend-contract.sh backend identifier validation."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "load-backend-contract.sh"


def test_rejects_path_traversal_in_backend_id():
    # Traversal attempting to escape config/backends/ must be rejected with exit code 1
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--backend", "../../manifest"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, (
        f"Expected exit code 1 for traversal attempt, got {proc.returncode}. Output:\n{proc.stdout}"
    )
    assert "Invalid backend ID" in proc.stderr, (
        f"Expected 'Invalid backend ID' error, got stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}"
    )


def test_rejects_parent_dir_in_backend_id():
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--backend", "../gpu-database"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 1, (
        f"Expected exit code 1 for traversal attempt, got {proc.returncode}. Output:\n{proc.stdout}"
    )
    assert "Invalid backend ID" in proc.stderr, (
        f"Expected 'Invalid backend ID' error, got stderr:\n{proc.stderr}"
    )


def test_accepts_valid_backend_id():
    proc = subprocess.run(
        ["bash", str(SCRIPT), "--backend", "cpu", "--env"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"Expected exit code 0 for valid backend 'cpu', got {proc.returncode}. Stderr:\n{proc.stderr}"
    assert 'BACKEND_CONTRACT_ID="cpu"' in proc.stdout, f"Expected BACKEND_CONTRACT_ID=\"cpu\", got:\n{proc.stdout}"


if __name__ == "__main__":
    test_rejects_path_traversal_in_backend_id()
    test_rejects_parent_dir_in_backend_id()
    test_accepts_valid_backend_id()
    print("[PASS] test_backend_contract_traversal")
