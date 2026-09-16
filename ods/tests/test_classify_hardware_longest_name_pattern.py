from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "classify-hardware.sh"


def _run_classify(**kwargs) -> dict[str, str]:
    cmd = ["bash", str(SCRIPT_PATH)]
    for k, v in kwargs.items():
        cmd.extend([f"--{k.replace('_', '-')}", str(v)])
    cmd.append("--env")
    proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
    env = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            key, val = line.split("=", 1)
            env[key.strip()] = val.strip().strip('"')
    return env


def test_longest_name_pattern_wins_regardless_of_order():
    custom_db = {
        "version": 1,
        "known_gpus": [
            {
                "id": "card_xt",
                "match": {"name_patterns": ["RX 7900 XT"]},
                "specs": {"tier": "T2", "backend": "amd"},
            },
            {
                "id": "card_xtx",
                "match": {"name_patterns": ["RX 7900 XTX"]},
                "specs": {"tier": "T3", "backend": "amd"},
            },
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(custom_db, f)
        db_path = f.name

    try:
        env = _run_classify(db=db_path, gpu_name="AMD Radeon RX 7900 XTX")
        assert env.get("HW_CLASS_ID") == "card_xtx"
    finally:
        Path(db_path).unlink(missing_ok=True)


def test_device_id_match_takes_precedence_over_longer_name():
    custom_db = {
        "version": 1,
        "known_gpus": [
            {
                "id": "card_by_id",
                "match": {"device_ids": ["0x744c"], "name_patterns": ["XT"]},
                "specs": {"tier": "T2", "backend": "amd"},
            },
            {
                "id": "card_by_longer_name",
                "match": {"name_patterns": ["RX 7900 XTX"]},
                "specs": {"tier": "T3", "backend": "amd"},
            },
        ],
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(custom_db, f)
        db_path = f.name

    try:
        env = _run_classify(
            db=db_path, device_id="0x744c", gpu_name="AMD Radeon RX 7900 XTX"
        )
        assert env.get("HW_CLASS_ID") == "card_by_id"
    finally:
        Path(db_path).unlink(missing_ok=True)


if __name__ == "__main__":
    test_longest_name_pattern_wins_regardless_of_order()
    test_device_id_match_takes_precedence_over_longer_name()
    print("All hardware classifier longest pattern tests passed.")
