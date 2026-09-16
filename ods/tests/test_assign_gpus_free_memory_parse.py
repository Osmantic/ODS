#!/usr/bin/env python3
"""Regression test: verify assign_gpus safely handles non-numeric memory_free_gb."""
import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parent.parent
if not (repo_root / "scripts").exists():
    repo_root = Path.cwd() / "ods" if (Path.cwd() / "ods").exists() else Path.cwd()
sys.path.insert(0, str(repo_root / "scripts"))

import assign_gpus

def test_free_memory_parsing_resilience():
    topology = {
        "gpus": [
            {
                "index": 0,
                "uuid": "GPU-1234",
                "name": "NVIDIA RTX 4090",
                "memory_gb": "24.0",
                "memory_free_gb": "N/A",
                "memory_type": "discrete"
            }
        ]
    }
    gpus = assign_gpus.parse_gpus(topology)
    assert len(gpus) == 1
    assert gpus[0].memory_mb == 24.0 * 1024

if __name__ == "__main__":
    test_free_memory_parsing_resilience()
    print("test_assign_gpus_free_memory_parse: PASS")
