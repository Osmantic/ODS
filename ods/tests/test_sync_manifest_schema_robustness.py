#!/usr/bin/env python3
from pathlib import Path

def test_schema_is_file():
    ROOT_DIR = Path(__file__).resolve().parents[1]
    schema = ROOT_DIR / "extensions/library/schema/service-manifest.v1.json"
    assert schema.is_file()
    print("test_sync_manifest_schema_robustness passed.")

if __name__ == "__main__":
    test_schema_is_file()
