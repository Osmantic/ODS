#!/usr/bin/env python3
"""llama.cpp image pins and CUDA split-mode contract.

1. Every llama.cpp image ODS ships is pinned by tag and sha256 digest.
   llama.cpp publishes a ghcr tag for only a fraction of its builds, and ODS
   once pinned a tag that was never published (server-cuda-b8648). A digest
   also stops a re-pushed tag from changing what an install runs. Each tag
   must resolve to one digest everywhere, and every copy of the NVIDIA and CPU
   defaults (installer pulls, tier maps, host agent fallback, catalog entries)
   must match the Compose default exactly.

2. The Intel and Apple Docker images and native Windows pin the same
   llama.cpp build as NVIDIA/CPU, and the Intel Arc local build's source
   defaults name it (that image is not built by the installer). The Arc build
   checks the tag's commit, and the Windows installer checks the release
   archive's SHA-256 for every tag it can download, before downloading.
   Neither Intel overlay sets SYCL_CACHE_PERSISTENT, which crashes the oneAPI
   2025.3 runtime in the b9014 image.

3. NVIDIA never gets `--split-mode row`. llama.cpp removed CUDA row split in
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


def build_of(ref: str) -> str:
    match = re.search(r"-(b\d+)(?:@|$)", ref)
    return match.group(1) if match else ""


def check_other_backends(errors: list[str]) -> None:
    """Intel/Apple images, Arc source defaults and native Windows pin the default build."""
    default_build = build_of(compose_default("docker-compose.nvidia.yml"))
    intel = compose_default("docker-compose.intel.yml")
    apple_match = re.search(r"^\s*image:\s*(\S+)", (ROOT_DIR / "docker-compose.apple.yml").read_text(encoding="utf-8"), re.M)
    apple = apple_match.group(1) if apple_match else ""
    for name, ref in (("docker-compose.intel.yml", intel), ("docker-compose.apple.yml", apple)):
        if build_of(ref) != default_build:
            errors.append(f"{name}: llama.cpp {build_of(ref) or ref!r} differs from the NVIDIA/CPU default {default_build}")

    arc = (ROOT_DIR / "docker-compose.arc.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT_DIR / "images/llama-sycl/Dockerfile").read_text(encoding="utf-8")
    arc_tag = re.search(r"LLAMA_TAG: \$\{LLAMA_TAG:-([^}]+)\}", arc)
    arc_commit = re.search(r"LLAMA_COMMIT: \$\{LLAMA_COMMIT-([^}]*)\}", arc)
    file_tag = re.search(r"^ARG LLAMA_TAG=(\S+)$", dockerfile, re.M)
    file_commit = re.search(r"^ARG LLAMA_COMMIT=(\S+)$", dockerfile, re.M)
    if not (arc_tag and file_tag and arc_tag.group(1) == file_tag.group(1) == default_build):
        errors.append(f"Arc build: compose/Dockerfile LLAMA_TAG must both be {default_build}")
    if not (arc_commit and file_commit and arc_commit.group(1) == file_commit.group(1)
            and re.fullmatch(r"[0-9a-f]{40}", file_commit.group(1))):
        errors.append("Arc build: compose/Dockerfile LLAMA_COMMIT must be the same full commit SHA")
    if "rev-parse HEAD" not in dockerfile or "LLAMA_COMMIT" not in dockerfile.split("rev-parse HEAD", 1)[1][:200]:
        errors.append("images/llama-sycl/Dockerfile: the clone must be checked against LLAMA_COMMIT")

    constants = (ROOT_DIR / "installers/windows/lib/constants.ps1").read_text(encoding="utf-8")
    tier_map = (ROOT_DIR / "installers/windows/lib/tier-map.ps1").read_text(encoding="utf-8")
    installer = (ROOT_DIR / "installers/windows/install-windows.ps1").read_text(encoding="utf-8")
    release = re.search(r'^\$script:LLAMA_CPP_RELEASE_TAG = "(b\d+)"', constants, re.M)
    table = re.search(r"\$script:LLAMA_CPP_VULKAN_SHA256 = @\{(.*?)\}", constants, re.S)
    sums = dict(re.findall(r'"(b\d+)"\s*=\s*"([0-9a-f]{64})"', table.group(1))) if table else {}
    windows_tags = {release.group(1)} if release else set()
    windows_tags.update(re.findall(r'\$runtimeTag = "(b\d+)"', tier_map))
    if not release or release.group(1) != default_build:
        errors.append(f"constants.ps1: native Windows llama.cpp must be {default_build}")
    for tag in sorted(windows_tags):
        if tag not in sums:
            errors.append(f"constants.ps1: no SHA-256 for the Windows Vulkan archive of {tag}")
    verify = installer.find("LLAMA_CPP_VULKAN_SHA256[$script:LLAMA_CPP_RELEASE_TAG]")
    download = installer.find("Invoke-DownloadWithRetry -Url $script:LLAMA_CPP_VULKAN_URL")
    hashing = installer.find("Get-FileHash -LiteralPath $llamaZip -Algorithm SHA256", max(download, 0))
    extract = installer.find("Invoke-ExtractionWithRetry -ZipPath $llamaZip")
    if not (0 <= verify < download < hashing < extract):
        errors.append("install-windows.ps1: the pinned SHA-256 must be looked up before the download and checked before extraction")

    for name in ("docker-compose.intel.yml", "docker-compose.arc.yml"):
        text = (ROOT_DIR / name).read_text(encoding="utf-8")
        if re.search(r"^\s*-\s*SYCL_CACHE_PERSISTENT=", text, re.M):
            errors.append(f"{name}: must not pass SYCL_CACHE_PERSISTENT to llama-server")
        if "ONEAPI_DEVICE_SELECTOR=${ONEAPI_DEVICE_SELECTOR:-level_zero:gpu}" not in text:
            errors.append(f"{name}: ONEAPI_DEVICE_SELECTOR must come from .env (level_zero:0 on multi-GPU hosts)")
    if re.search(r"^SYCL_CACHE_PERSISTENT=", (ROOT_DIR / "installers/phases/06-directories.sh").read_text(encoding="utf-8"), re.M):
        errors.append("06-directories.sh: must not write SYCL_CACHE_PERSISTENT")


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
    check_other_backends(errors)
    check_split_mode(errors)
    if errors:
        print("[FAIL] llama.cpp image pin / split-mode contract")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("[PASS] llama.cpp images are tag@digest pinned and agree; Intel/Apple/Windows pin the default build; NVIDIA never uses row split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
