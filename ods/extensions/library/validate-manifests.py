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
LOCAL_SCHEMA_PATHS = {
    "ods.services.v1": SCRIPT_DIR / "schema" / "service-manifest.v1.json",
    "ods.services.v2": SCRIPT_DIR / "schema" / "service-manifest.v2.json",
}
SERVICES_DIR = SCRIPT_DIR / "services"


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects ambiguous duplicate mapping keys."""


def construct_unique_mapping(loader, node, deep=False):
    loader.flatten_mapping(node)
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable mapping key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key: {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_unique_mapping
)


def schema_path(schema_version="ods.services.v1"):
    """Use the repository contract when available, with a standalone fallback."""
    if schema_version not in LOCAL_SCHEMA_PATHS:
        raise ValueError(f"Unsupported manifest schema: {schema_version}")
    if not MANIFEST_FILE.is_file():
        return LOCAL_SCHEMA_PATHS[schema_version]

    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    configured = manifest["contracts"]["extensions"]["serviceManifestSchemas"]
    return ROOT_DIR / configured[schema_version]


def main():
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
            with open(manifest_path, encoding="utf-8") as f:
                data = yaml.load(f, Loader=UniqueKeyLoader)
        except (OSError, UnicodeError, yaml.YAMLError) as e:
            print(f"FAIL  {service_name}: YAML read or parse error: {e}")
            failed += 1
            continue

        if data is None:
            print(f"FAIL  {service_name}: Empty manifest")
            failed += 1
            continue

        schema_version = data.get("schema_version") if isinstance(data, dict) else None
        try:
            schema_path_value = schema_path(schema_version)
            with open(schema_path_value, encoding="utf-8") as schema_file:
                schema = json.load(schema_file)
            validator_cls = jsonschema.validators.validator_for(schema)
            validator_cls.check_schema(schema)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"FAIL  {service_name}: Cannot resolve manifest schema: {exc}")
            failed += 1
            continue
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
