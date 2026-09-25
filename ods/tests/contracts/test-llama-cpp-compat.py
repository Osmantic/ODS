#!/usr/bin/env python3
"""llama.cpp image pins and CUDA split-mode contract.

1. Every llama.cpp image ODS ships is pinned by tag and sha256 digest.
   llama.cpp publishes a ghcr tag for only a fraction of its builds, and ODS
   once pinned a tag that was never published (server-cuda-b8648). A digest
   also stops a re-pushed tag from changing what an install runs. Each tag
   must resolve to one digest everywhere, and every copy of the NVIDIA and CPU
   defaults (installer pulls, tier maps, host agent fallback, catalog entries)
   must match the Compose default exactly.

2. NVIDIA never gets `--split-mode row`. llama.cpp removed CUDA row split in
   b9890 (ggml-org/llama.cpp#24216): the flag still parses, but model load
   fails with "does not support split buffers". ODS maps tensor and hybrid
   assignments to layer split on NVIDIA. AMD (Lemonade) is unchanged.

Run from ods/:  python3 tests/contracts/test-llama-cpp-compat.py
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
IMAGE_RE = re.compile(r"ghcr\.io/ggml-org/llama\.cpp:(?P<tag>[A-Za-z0-9._-]+)(?P<digest>@sha256:[0-9a-f]{64})?")
DEFAULT_RE = re.compile(r"image:\s*\$\{LLAMA_SERVER_IMAGE:-(?P<ref>[^}]+)\}")
SCANNED_SUFFIXES = {".py", ".sh", ".ps1", ".psm1", ".yml", ".yaml", ".json", ".example", ""}
SKIPPED_PREFIXES = ("tests/", "vendor/", "node_modules/", "data/", "docs/")

# Every place that repeats a default image outside Compose.
NVIDIA_COPIES = (
    "installers/lib/tier-map.sh",
    "installers/windows/lib/tier-map.ps1",
    "installers/phases/08-images.sh",
    "bin/ods-host-agent.py",
    "config/dependency-lock.json",
)
CPU_COPIES = (
    "docker-compose.base.yml",
    "installers/phases/08-images.sh",
    "installers/phases/11-services.sh",
    "config/dependency-lock.json",
)


def shipped_files() -> list[Path]:
    files = []
    for path in sorted(ROOT_DIR.rglob("*")):
        relative = path.relative_to(ROOT_DIR).as_posix()
        if not path.is_file() or relative.startswith(SKIPPED_PREFIXES) or "/tests/" in relative:
            continue
        if "/node_modules/" in relative or path.suffix not in SCANNED_SUFFIXES:
            continue
        files.append(path)
    return files


def compose_default(name: str) -> str:
    match = DEFAULT_RE.search((ROOT_DIR / name).read_text(encoding="utf-8"))
    return match.group("ref") if match else ""


def check_pins(errors: list[str]) -> None:
    digests_by_tag: dict[str, set[str]] = {}
    for path in shipped_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(ROOT_DIR).as_posix()
        for line_no, line in enumerate(text.splitlines(), start=1):
            for match in IMAGE_RE.finditer(line):
                if not match.group("digest"):
                    errors.append(f"{relative}:{line_no}: llama.cpp image without @sha256 digest: {match.group(0)}")
                    continue
                digests_by_tag.setdefault(match.group("tag"), set()).add(match.group("digest"))
    for tag, digests in sorted(digests_by_tag.items()):
        if len(digests) > 1:
            errors.append(f"llama.cpp:{tag} is pinned to {len(digests)} different digests: {sorted(digests)}")

    nvidia = compose_default("docker-compose.nvidia.yml")
    cpu = compose_default("docker-compose.cpu.yml")
    for name, ref in (("docker-compose.nvidia.yml", nvidia), ("docker-compose.cpu.yml", cpu)):
        if not IMAGE_RE.fullmatch(ref) or "@sha256:" not in ref:
            errors.append(f"{name}: default llama.cpp image must be tag@digest, got {ref!r}")
    for copies, ref, label in ((NVIDIA_COPIES, nvidia, "NVIDIA"), (CPU_COPIES, cpu, "CPU")):
        for relative in copies:
            if ref and ref not in (ROOT_DIR / relative).read_text(encoding="utf-8"):
                errors.append(f"{relative}: does not repeat the {label} Compose default {ref}")


def bash_case(text: str, anchor: str, variables: dict[str, str], result: str) -> str:
    """Run the `case` statement that starts at `anchor` with the given inputs."""
    start = text.index(anchor)
    end = text.index("esac", start) + len("esac")
    assignments = "".join(f"{name}={value!r}\n" for name, value in variables.items())
    script = f"error() {{ echo ERROR; }}\n{assignments}{text[start:end]}\nprintf '%s' \"${result}\"\n"
    # Bytes keep LF line endings on every host; bash rejects CRLF scripts.
    completed = subprocess.run(["bash"], input=script.encode("utf-8"), capture_output=True, check=True)
    return completed.stdout.decode("utf-8")


def check_split_mode(errors: list[str]) -> None:
    features = (ROOT_DIR / "installers/phases/03-features.sh").read_text(encoding="utf-8")
    cli = (ROOT_DIR / "ods-cli").read_text(encoding="utf-8")
    cases = (
        ("installers/phases/03-features.sh", features, 'case "$_mode" in', "_mode", "VENDOR", "LLAMA_ARG_SPLIT_MODE"),
        ("ods-cli (auto)", cli, 'case "$_para_mode" in', "_para_mode", "backend", "split_mode"),
        ("ods-cli (manual)", cli, 'case "${_para:-tensor}" in', "_para", "backend", "split_mode_m"),
    )
    for label, text, anchor, mode_var, vendor_var, result in cases:
        for mode in ("tensor", "hybrid", "pipeline"):
            for vendor, expected in (("nvidia", "layer"), ("amd", "row" if mode != "pipeline" else "layer")):
                inputs = {mode_var: mode, vendor_var: vendor, "llama_gpu_count": "2"}
                actual = bash_case(text, anchor, inputs, result)
                if actual != expected:
                    errors.append(f"{label}: {vendor} {mode} assignment maps to split mode {actual!r}, expected {expected!r}")


def main() -> int:
    errors: list[str] = []
    check_pins(errors)
    check_split_mode(errors)
    if errors:
        print("[FAIL] llama.cpp image pin / split-mode contract")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("[PASS] llama.cpp images are tag@digest pinned and agree; NVIDIA never uses row split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
