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
ROOT_DIR = SCRIPT_DIR.parent.parent
MANIFEST_FILE = ROOT_DIR / "manifest.json"
LOCAL_SCHEMA_PATH = SCRIPT_DIR / "schema" / "service-manifest.v1.json"
SERVICES_DIR = SCRIPT_DIR / "services"

# Max manifest file size: 1MB (prevents DoS via file exhaustion)
MAX_MANIFEST_SIZE = 1024 * 1024


def schema_path():
    """Use the repository contract when available, with a standalone fallback."""
    if not MANIFEST_FILE.is_file():
        return LOCAL_SCHEMA_PATH

    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    return ROOT_DIR / manifest["contracts"]["extensions"]["serviceManifestSchema"]


def main():
    # Load schema
    try:
        schema_path_value = schema_path()
    except (KeyError, OSError, TypeError, json.JSONDecodeError) as exc:
        print(f"ERROR: Cannot resolve canonical schema from {MANIFEST_FILE}: {exc}")
        sys.exit(2)
    if not schema_path_value.exists():
        print(f"ERROR: Schema not found at {schema_path_value}")
        sys.exit(2)

    with open(schema_path_value, encoding="utf-8") as f:
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
            # Check file size to prevent DoS via large files
            file_size = manifest_path.stat().st_size
            if file_size > MAX_MANIFEST_SIZE:
                print(f"FAIL  {service_name}: Manifest file too large ({file_size} bytes, max {MAX_MANIFEST_SIZE})")
                failed += 1
                continue

            with open(manifest_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except OSError as e:
            print(f"FAIL  {service_name}: File read error: {e}")
            failed += 1
            continue
        except yaml.YAMLError as e:
            print(f"FAIL  {service_name}: YAML parse error: {e}")
            failed += 1
            continue

        if data is None:
            print(f"FAIL  {service_name}: Empty manifest")
            failed += 1
            continue

        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        errors = list(validator_cls(schema).iter_errors(data))
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
