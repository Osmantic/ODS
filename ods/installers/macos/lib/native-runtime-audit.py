"""Audit a flat ARM64 llama.cpp bundle before portability qualification.

This checks loader metadata, not behavior on older hardware or OS releases.
It never executes the supplied binaries or modifies an installed runtime.
"""
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


def version(value):
    if not re.fullmatch(r"\d+(?:\.\d+){0,2}", value):
        raise ValueError("Invalid macOS version")
    parts = tuple(map(int, value.split(".")))
    return parts + (0,) * (3 - len(parts))


def inspect_metadata(binary, root, architectures, commands, libraries, minimum):
    errors = []
    if architectures.split() != ["arm64"]:
        errors.append("Expected an ARM64-only qualification bundle")
    targets = re.findall(r"^\s*minos\s+(\S+)", commands, re.M)
    targets += re.findall(r"cmd LC_VERSION_MIN_MACOSX\s+cmdsize \d+\s+version (\S+)", commands)
    if len(targets) != 1 or version(targets[0]) > version(minimum):
        errors.append("Missing or incompatible minimum macOS version")
    rpaths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", commands)
    identities = re.findall(r"cmd LC_ID_DYLIB\s+cmdsize \d+\s+name (.*?) \(offset", commands)
    if any(path != "@loader_path" for path in rpaths):
        errors.append("Non-local runtime search path")
    for line in libraries.splitlines()[1:]:
        dependency = line.strip().split(" (compatibility version", 1)[0]
        if not dependency:
            continue
        if dependency in identities:
            continue
        if ".." in PurePosixPath(dependency).parts:
            errors.append("Parent traversal in dependency: " + dependency)
            continue
        if dependency.startswith(("/usr/lib/", "/System/Library/")):
            continue
        if dependency.startswith("@rpath/") and "@loader_path" in rpaths:
            candidate = binary.parent / dependency[len("@rpath/"):]
        elif dependency.startswith("@loader_path/"):
            candidate = binary.parent / dependency[len("@loader_path/"):]
        else:
            errors.append("External or unsupported dependency: " + dependency)
            continue
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root) or not resolved.is_file():
            errors.append("Missing or escaping bundle dependency: " + dependency)
    return errors


def audit(root, minimum):
    root = root.resolve(strict=True)
    version(minimum)
    files = [root / "llama-server", *sorted(root.glob("*.dylib"))]
    report = []
    seen = set()
    for path in files:
        binary = path.resolve(strict=True)
        if not binary.is_relative_to(root):
            raise ValueError("Bundle symlink escapes its directory")
        if binary in seen:
            continue
        seen.add(binary)
        def output(tool, *args):
            return subprocess.check_output([tool, *args, str(binary)], text=True, timeout=30)
        errors = inspect_metadata(binary, root, output("/usr/bin/lipo", "-archs"),
                                  output("/usr/bin/otool", "-l"),
                                  output("/usr/bin/otool", "-L"), minimum)
        report.append({"file": path.name, "errors": errors})
    return {"scope": "ARM64 loader metadata only; hardware qualification still required",
            "maximumDeploymentTarget": minimum, "files": report,
            "passed": all(not row["errors"] for row in report)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--minimum-macos", default="14.0")
    args = parser.parse_args()
    try:
        report = audit(args.bundle, args.minimum_macos)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        report = {"passed": False, "error": str(error)}
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
