#!/usr/bin/env python3
"""Register the proved Windows model store before composing this WSL install."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE / "bin"))
sys.path.insert(0, str(SOURCE / "extensions/services/dashboard-api"))
from env_values import parse_env_value
from model_stores import registered_stores
from model_switchboard import wsl_lemonade

spec = importlib.util.spec_from_file_location("ods_register_wsl_store", SOURCE / "scripts/register-model-store.py")
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)


def _env(install_dir: Path) -> dict:
    values = {}
    for line in (install_dir / ".env").read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() in {"LEMONADE_HOST_TRANSPORT", "LEMONADE_BASE_URL",
                                         "LEMONADE_CONTAINER_BASE_URL", "AMD_INFERENCE_PORT", "ODS_WINDOWS_SYSTEM_DIRECTORY"}:
            values[key.strip()] = parse_env_value(value)
    return values


def _previous(path: Path) -> dict | None:
    if path.is_symlink():
        raise ValueError("Refusing symlinked WSL runtime metadata")
    if not path.exists():
        return None
    with path.open("rb") as source:
        data = source.read(65537)
    if len(data) > 65536:
        raise ValueError("WSL runtime metadata exceeds 64 KiB")
    value = json.loads(data)
    if (not isinstance(value, dict) or set(value) != {"schemaVersion", "planPath", "modelStoreId"}
            or value["schemaVersion"] != 1 or not isinstance(value["planPath"], str)
            or not Path(value["planPath"]).is_absolute() or not isinstance(value["modelStoreId"], str)):
        raise ValueError("Invalid WSL runtime metadata")
    return value


def _write_metadata(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError("Refusing symlinked WSL runtime metadata")
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def configure(install_dir: Path, env: dict | None = None) -> dict:
    root = Path(install_dir).resolve(strict=True)
    values = _env(root) if env is None else env
    if not wsl_lemonade.candidate(values):
        return {"configured": False, "reason": "not_wsl_lemonade"}
    metadata = root / "data/wsl-lemonade-runtime.json"
    previous = _previous(metadata)
    try:
        proof = wsl_lemonade.status(root, values)
        if not proof["managed"]:
            if previous:
                raise ValueError("The previously registered Windows Lemonade task is no longer owned")
            return {"configured": False, "reason": "external_runtime_unmanaged"}
        models = wsl_lemonade.model_store(root, values, proof)
        plan = wsl_lemonade.plan_path(root, values, proof)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        if previous:
            raise
        return {"configured": False, "reason": "windows_runtime_proof_unavailable"}
    if previous and previous["planPath"] != plan.as_posix():
        raise ValueError("The Windows runtime plan path changed; explicit migration is required")
    same_path = [store for store in registered_stores(root / "data")
                 if store["id"] != "default" and store["path"] == models]
    if len(same_path) > 1:
        raise ValueError("The Windows model directory has ambiguous registrations")
    identifier = same_path[0]["id"] if same_path else "windows-lemonade"
    registration.register(root, identifier, models)
    _write_metadata(metadata, {"schemaVersion": 1, "planPath": plan.as_posix(), "modelStoreId": identifier})
    return {"configured": True, "modelStoreId": identifier}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(configure(args.install_dir)))
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Windows Lemonade model store could not be configured: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
