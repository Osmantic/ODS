#!/usr/bin/env python3
"""Validate all service manifests against the JSON schema."""

import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:
    print("ERROR: jsonschema package not installed. Run: pip install jsonschema")
    sys.exit(2)

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML package not installed. Run: pip install pyyaml")
    sys.exit(2)

SCRIPT_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = SCRIPT_DIR / "schema" / "service-manifest.v1.json"
SERVICES_DIR = SCRIPT_DIR / "services"


def main():
    # Load schema
    if not SCHEMA_PATH.exists():
        print(f"ERROR: Schema not found at {SCHEMA_PATH}")
        sys.exit(2)

    with open(SCHEMA_PATH) as f:
        schema = json.load(f)

    if not SERVICES_DIR.is_dir():
        print(f"ERROR: Services directory not found: {SERVICES_DIR}")
        sys.exit(2)

    # Find manifests
    manifests = sorted(SERVICES_DIR.glob("*/manifest.yaml"))
    if not manifests:
        print("WARNING: No manifest files found")
        sys.exit(0)

    total = 0
    passed = 0
    failed = 0

    for manifest_path in manifests:
        service_name = manifest_path.parent.name
        total += 1

        try:
            with open(manifest_path) as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            print(f"FAIL  {service_name}: YAML parse error: {e}")
            failed += 1
            continue

        if data is None:
            print(f"FAIL  {service_name}: Empty manifest")
            failed += 1
            continue

        errors = list(jsonschema.Draft7Validator(schema).iter_errors(data))
        if errors:
            failed += 1
            print(f"FAIL  {service_name}:")
            for err in errors:
                path = ".".join(str(p) for p in err.absolute_path) or "(root)"
                print(f"        {path}: {err.message}")
        else:
            passed += 1
            print(f"PASS  {service_name}")

    print(f"\n{'=' * 40}")
    print(f"Total: {total}  Passed: {passed}  Failed: {failed}")

    sys.exit(1 if failed > 0 else 0)


if __name__ == "__main__":
    main()
