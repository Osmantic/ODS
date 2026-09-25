#!/usr/bin/env python3
"""Every service port knob a manifest declares must be documented and declared.

A service manifest's ``external_port_env`` is the name users set to move that
service off its default port. Three files have to agree about it:

  * ``extensions/services/<id>/manifest.yaml`` declares the name and default;
  * ``.env.example`` documents it, so users can discover it without reading
    manifests;
  * ``.env.schema.json`` declares it, so ``scripts/validate-env.sh`` accepts it
    rather than flagging it as unknown.

This contract catches a new service that ships a port knob nobody can find, and
a default that drifts between the manifest and the documented example.

Run: python3 tests/contracts/test-port-env-documentation.py
"""

import json
import pathlib
import re
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
SERVICES_DIR = ROOT / "extensions" / "services"
ENV_EXAMPLE = ROOT / ".env.example"
ENV_SCHEMA = ROOT / ".env.schema.json"


def manifest_port_envs():
    """Return [(service_id, env_name, default_port)] for every declared knob."""
    entries = []
    for service_dir in sorted(SERVICES_DIR.iterdir()):
        manifest = service_dir / "manifest.yaml"
        if not manifest.is_dir() and manifest.exists():
            loaded = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
            service = loaded.get("service") or {}
            env_name = service.get("external_port_env")
            if not env_name:
                continue
            default = service.get("external_port_default", service.get("port"))
            entries.append((service.get("id", service_dir.name), env_name, default))
    return entries


def documented_value(lines, key):
    """Return the value .env.example documents for *key*, commented or not."""
    pattern = re.compile(r"^\s*#?\s*" + re.escape(key) + r"=([^\s#]*)")
    for line in lines:
        match = pattern.match(line)
        if match:
            return match.group(1)
    return None


def main():
    entries = manifest_port_envs()
    if not entries:
        print("[FAIL] no service manifest declares external_port_env", file=sys.stderr)
        return 1

    env_lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    schema_properties = set(json.loads(ENV_SCHEMA.read_text(encoding="utf-8"))["properties"])

    failures = []
    for service_id, env_name, default in entries:
        documented = documented_value(env_lines, env_name)
        if documented is None:
            failures.append(
                f"{service_id}: {env_name} is not documented in .env.example"
            )
        elif documented != str(default):
            failures.append(
                f"{service_id}: .env.example documents {env_name}={documented} "
                f"but the manifest default is {default}"
            )
        if env_name not in schema_properties:
            failures.append(
                f"{service_id}: {env_name} is not declared in .env.schema.json"
            )

    for failure in failures:
        print(f"[FAIL] {failure}", file=sys.stderr)

    if failures:
        print(f"\n{len(failures)} problem(s) across {len(entries)} port knobs", file=sys.stderr)
        return 1

    print(f"[PASS] {len(entries)} service port knobs are documented and declared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
