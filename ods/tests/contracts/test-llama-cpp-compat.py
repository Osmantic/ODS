#!/usr/bin/env python3
"""llama.cpp image pins and CUDA split-mode contract.

1. Every llama.cpp image ODS ships is pinned by tag and sha256 digest.
   llama.cpp publishes a ghcr tag for only a fraction of its builds, and ODS
   once pinned a tag that was never published (server-cuda-b8648). A digest
   also stops a re-pushed tag from changing what an install runs. Each tag
   must resolve to one digest everywhere, and every copy of the NVIDIA and CPU
   defaults (installer pulls, tier maps, host agent fallback, dependency lock)
   must match the Compose default exactly. Explicit per-model catalog runtime
   overrides have separate release boundaries and retain their own pins.

2. Each backend pins its explicitly selected build in BACKEND_BUILDS.
   NVIDIA CUDA advances independently for the Qwen3.5 NextN loader; CPU,
   AMD, Intel, Apple, Arc and native Windows keep their existing builds.
   This table is a release policy, not a runtime or fleet qualification claim.
   The AMD images are official ggml-org images whose every copy
   (Compose, installer pulls, config/backends/amd.json, the lock file)
   agrees; amd.json's Windows archive pin matches the lock and the Windows
   installer's SHA-256 table. The Arc build checks the tag's commit, and the
   Windows installer checks the release archive's SHA-256 for every tag it
   can download, before downloading. Neither Intel overlay sets
   SYCL_CACHE_PERSISTENT, which crashes the oneAPI 2025.3 runtime in the
   b9014 image.

3. No GPU gets `--split-mode row`. llama.cpp removed CUDA row split in
   b9890 (ggml-org/llama.cpp#24216): the flag still parses, but model load
   fails with "does not support split buffers"; Vulkan has no row split, and
   the HIP backend shares CUDA's split code. ODS maps tensor and hybrid
   assignments to layer split on NVIDIA and AMD.

Run from ods/:  python3 tests/contracts/test-llama-cpp-compat.py
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[2]
IMAGE_RE = re.compile(r"ghcr\.io/ggml-org/llama\.cpp:(?P<tag>[A-Za-z0-9._-]+)(?P<digest>@sha256:[0-9a-f]{64})?")
DEFAULT_RE = re.compile(r"image:\s*\$\{LLAMA_SERVER_IMAGE:-(?P<ref>[^}]+)\}")
SCANNED_SUFFIXES = {".py", ".sh", ".ps1", ".psm1", ".yml", ".yaml", ".json", ".example", ""}
SKIPPED_PREFIXES = ("tests/", "vendor/", "node_modules/", "data/", "docs/")

# Deliberate backend release boundaries. Never derive another backend's
# expected build from NVIDIA: advancing CUDA does not qualify that backend.
BACKEND_BUILDS = {
    "nvidia": "b11429",
    "cpu": "b9014",
    "amd-vulkan": "b9014",
    "amd-rocm": "b9014",
    "intel": "b9014",
    "apple": "b9014",
    "arc": "b9014",
    "windows-native": "b9014",
}

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
# The AMD overlays pin their image directly (they must not read
# LLAMA_SERVER_IMAGE), so their Compose line is a copy like the others.
AMD_COPIES = (
    "installers/phases/08-images.sh",
    "config/backends/amd.json",
    "config/dependency-lock.json",
)
AMD_IMAGES = (
    ("docker-compose.amd.yml", "server-vulkan-", "linux_image"),
    ("docker-compose.amd-rocm.yml", "server-rocm-", "linux_rocm_image"),
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


def compose_image(name: str) -> str:
    """The llama-server image of an overlay that pins it without a variable."""
    match = re.search(r"^    image:\s*(\S+)", (ROOT_DIR / name).read_text(encoding="utf-8"), re.M)
    return match.group(1) if match else ""


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

    contract = json.loads((ROOT_DIR / "config/backends/amd.json").read_text(encoding="utf-8"))
    runtime = contract.get("runtime", {}).get("llama_server", {})
    for name, tag_prefix, key in AMD_IMAGES:
        ref = compose_image(name)
        match = IMAGE_RE.fullmatch(ref)
        # A literal reference: a variable such as LLAMA_SERVER_IMAGE could name
        # another backend's image.
        if not match or not match.group("digest") or not match.group("tag").startswith(tag_prefix):
            errors.append(f"{name}: llama-server image must be a literal official ggml-org {tag_prefix}* tag@digest, got {ref!r}")
            continue
        if runtime.get(key) != ref:
            errors.append(f"config/backends/amd.json: runtime.llama_server.{key} must be {ref}")
        for relative in AMD_COPIES:
            if ref not in (ROOT_DIR / relative).read_text(encoding="utf-8"):
                errors.append(f"{relative}: does not repeat the AMD image {ref}")


def build_of(ref: str) -> str:
    match = re.search(r"-(b\d+)(?:@|$)", ref)
    return match.group(1) if match else ""


def check_other_backends(errors: list[str]) -> None:
    """Keep every backend on its own explicit release, including native pins."""
    intel = compose_default("docker-compose.intel.yml")
    apple_match = re.search(r"^\s*image:\s*(\S+)", (ROOT_DIR / "docker-compose.apple.yml").read_text(encoding="utf-8"), re.M)
    apple = apple_match.group(1) if apple_match else ""
    refs = (
        ("nvidia", "docker-compose.nvidia.yml", compose_default("docker-compose.nvidia.yml")),
        ("cpu", "docker-compose.cpu.yml", compose_default("docker-compose.cpu.yml")),
        ("intel", "docker-compose.intel.yml", intel),
        ("apple", "docker-compose.apple.yml", apple),
        ("amd-vulkan", "docker-compose.amd.yml", compose_image("docker-compose.amd.yml")),
        ("amd-rocm", "docker-compose.amd-rocm.yml", compose_image("docker-compose.amd-rocm.yml")),
    )
    for backend, name, ref in refs:
        expected_build = BACKEND_BUILDS[backend]
        if build_of(ref) != expected_build:
            errors.append(f"{name}: llama.cpp {build_of(ref) or ref!r} differs from the {backend} release policy {expected_build}")

    arc = (ROOT_DIR / "docker-compose.arc.yml").read_text(encoding="utf-8")
    dockerfile = (ROOT_DIR / "images/llama-sycl/Dockerfile").read_text(encoding="utf-8")
    arc_tag = re.search(r"LLAMA_TAG: \$\{LLAMA_TAG:-([^}]+)\}", arc)
    arc_commit = re.search(r"LLAMA_COMMIT: \$\{LLAMA_COMMIT-([^}]*)\}", arc)
    file_tag = re.search(r"^ARG LLAMA_TAG=(\S+)$", dockerfile, re.M)
    file_commit = re.search(r"^ARG LLAMA_COMMIT=(\S+)$", dockerfile, re.M)
    arc_build = BACKEND_BUILDS["arc"]
    if not (arc_tag and file_tag and arc_tag.group(1) == file_tag.group(1) == arc_build):
        errors.append(f"Arc build: compose/Dockerfile LLAMA_TAG must both be {arc_build}")
    if not (arc_commit and file_commit and arc_commit.group(1) == file_commit.group(1)
            and re.fullmatch(r"[0-9a-f]{40}", file_commit.group(1))):
        errors.append("Arc build: compose/Dockerfile LLAMA_COMMIT must be the same full commit SHA")
    if "rev-parse HEAD" not in dockerfile or "LLAMA_COMMIT" not in dockerfile.split("rev-parse HEAD", 1)[1][:200]:
        errors.append("images/llama-sycl/Dockerfile: the clone must be checked against LLAMA_COMMIT")

    constants = (ROOT_DIR / "installers/windows/lib/constants.ps1").read_text(encoding="utf-8")
    tier_map = (ROOT_DIR / "installers/windows/lib/tier-map.ps1").read_text(encoding="utf-8")
    runtime = (ROOT_DIR / "installers/windows/lib/native-llama-runtime.ps1").read_text(encoding="utf-8")
    release = re.search(r'^\$script:LLAMA_CPP_RELEASE_TAG = "(b\d+)"', constants, re.M)
    table = re.search(r"\$script:LLAMA_CPP_VULKAN_SHA256 = @\{(.*?)\}", constants, re.S)
    sums = dict(re.findall(r'"(b\d+)"\s*=\s*"([0-9a-f]{64})"', table.group(1))) if table else {}
    windows_tags = {release.group(1)} if release else set()
    windows_tags.update(re.findall(r'\$runtimeTag = "(b\d+)"', tier_map))
    windows_build = BACKEND_BUILDS["windows-native"]
    if not release or release.group(1) != windows_build:
        errors.append(f"constants.ps1: native Windows llama.cpp must be {windows_build}")
    for tag in sorted(windows_tags):
        if tag != windows_build:
            errors.append(f"Windows native runtime tag {tag} differs from its release policy {windows_build}")
        if tag not in sums:
            errors.append(f"constants.ps1: no SHA-256 for the Windows Vulkan archive of {tag}")
    # Both Windows installers acquire llama-server.exe through
    # Install-ODSNativeLlamaRuntime, whose pin carries the size and SHA-256.
    install = runtime.partition("function Install-ODSNativeLlamaRuntime")[2]
    download = install.find("Invoke-ODSNativeLlamaDownload $Pin.Url $zip")
    size = install.find("-ne $Pin.Size", max(download, 0))
    hashing = install.find("-cne $Pin.Sha256", max(download, 0))
    extract = install.find("Expand-ODSNativeLlamaArchive $zip", max(download, 0))
    if not (0 <= download < size < hashing < extract):
        errors.append("native-llama-runtime.ps1: the download's pinned size and SHA-256 must be checked before extraction")

    windows_pin = json.loads((ROOT_DIR / "config/backends/amd.json").read_text(encoding="utf-8")) \
        .get("runtime", {}).get("llama_server", {}).get("windows", {})
    lock = json.loads((ROOT_DIR / "config/dependency-lock.json").read_text(encoding="utf-8"))
    lock_archive = {entry.get("id"): entry.get("value") for entry in lock.get("entries", [])}
    pin_tag = windows_pin.get("release_tag")
    if pin_tag != windows_build:
        errors.append(f"amd.json: runtime.llama_server.windows.release_tag must be {windows_build}")
    if windows_pin.get("asset") != f"llama-{pin_tag}-bin-win-vulkan-x64.zip":
        errors.append("amd.json: runtime.llama_server.windows.asset must be the release's win-vulkan-x64 zip")
    if not re.fullmatch(r"[0-9a-f]{64}", str(windows_pin.get("sha256", ""))) \
            or windows_pin.get("sha256") != lock_archive.get("amd.llama-server-windows-vulkan"):
        errors.append("amd.json: runtime.llama_server.windows.sha256 must match the dependency-lock archive entry")
    if sums.get(pin_tag) and windows_pin.get("sha256") != sums[pin_tag]:
        errors.append(f"amd.json and constants.ps1 disagree on the {pin_tag} Windows archive SHA-256")
    if not isinstance(windows_pin.get("size"), int) or windows_pin["size"] <= 0:
        errors.append("amd.json: runtime.llama_server.windows.size must be the archive size in bytes")

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
            for vendor, expected in (("nvidia", "layer"), ("amd", "layer")):
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
    print("[PASS] llama.cpp images are tag@digest pinned and copies agree; each backend matches its explicit release policy; no GPU uses row split")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
