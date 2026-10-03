"""Warn about native inference competing with the Docker VM for unified RAM.

This is a capacity estimate, not a measurement of resident model memory. It
does not modify Docker settings, select a smaller model, or disable services.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

GIB = 1024 ** 3


def assess(host_bytes, docker_bytes, model_bytes):
    if host_bytes <= 0 or model_bytes <= 0 or docker_bytes is not None and docker_bytes <= 0:
        raise ValueError("invalid memory capacity")
    # Explicit planning allowances: context/KV/compute buffers vary by model.
    native_allowance = 2 * GIB
    macos_allowance = 3 * GIB
    planned = model_bytes + native_allowance + macos_allowance
    if docker_bytes is not None:
        planned += docker_bytes
    return {
        "hostBytes": host_bytes,
        "dockerCapacityBytes": docker_bytes,
        "modelFileBytes": model_bytes,
        "nativeAllowanceBytes": native_allowance,
        "macosAllowanceBytes": macos_allowance,
        "plannedBytes": planned,
        "risk": "overcommitted" if planned > host_bytes else
                "unknown" if docker_bytes is None else "within-estimate",
    }


def command_integer(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=True)
    value = int(result.stdout.strip())
    if value <= 0:
        raise ValueError("invalid capacity")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        host = command_integer(["/usr/sbin/sysctl", "-n", "hw.memsize"])
        model = args.model.stat().st_size
        try:
            docker = command_integer(["docker", "info", "--format", "{{.MemTotal}}"])
        except (OSError, ValueError, subprocess.SubprocessError):
            docker = None
        report = assess(host, docker, model)
    except (OSError, ValueError, subprocess.SubprocessError):
        print("ODS: native memory estimate unavailable; inspect Docker and macOS memory before loading the model.", file=sys.stderr)
        return 0
    if args.json:
        print(json.dumps(report))
    elif report["risk"] == "overcommitted":
        docker_text = f"{docker / GIB:.1f} GiB Docker VM capacity + " if docker is not None else "Docker capacity unknown + "
        print("ODS memory warning: " + docker_text +
              f"{model / GIB:.1f} GiB model file + 2 GiB native runtime allowance + "
              f"3 GiB macOS allowance exceeds {host / GIB:.1f} GiB physical RAM. "
              "This is an estimate, not current RAM use. Review Docker Resources "
              "and optional ODS services before loading this model; no settings were changed.", file=sys.stderr)
    elif docker is None:
        print("ODS: Docker VM capacity is unknown; native memory headroom cannot be confirmed.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
