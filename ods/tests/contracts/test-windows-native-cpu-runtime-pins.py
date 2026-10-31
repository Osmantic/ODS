"""Linux-side contract for the Windows-native llama.cpp CPU pins and launch profile.

config/backends/cpu.json pins the official b9014 win-cpu-x64 and win-cpu-arm64
release zips. This checks, without PowerShell, that the pins, the measured zip
listing (tests/fixtures/windows-native/llama-cpp-b9014-cpu), the dependency
lock, installers/windows/lib/native-llama-runtime.ps1 and its PowerShell
contract agree, and that only the native-app launch profile turns the built-in
web UI on. The PowerShell behaviour itself is tested by
tests/contracts/test-windows-native-llama-runtime.ps1 (pwsh in CI).
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "installers/windows/lib/native-llama-runtime.ps1"
PS_CONTRACT = ROOT / "tests/contracts/test-windows-native-llama-runtime.ps1"
FETCH = ROOT / "installers/windows/native/fetch-runtime.ps1"
LISTING = ROOT / "tests/fixtures/windows-native/llama-cpp-b9014-cpu/zip-entries.txt"
PINS = (("windows", "x64"), ("windows_arm64", "arm64"))
ONE_MIB = 1024 * 1024


def load_json(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def cpu_pins() -> dict:
    return load_json("config/backends/cpu.json")["runtime"]["llama_server"]


def zip_listing() -> dict:
    listing: dict = {}
    current = ""
    for line in LISTING.read_text(encoding="utf-8").splitlines():
        header = re.fullmatch(r"(\S+\.zip) sha256=([0-9a-f]{64}) bytes=([0-9]+)", line)
        if header:
            current = header.group(1)
            listing[current] = {"sha256": header.group(2), "size": int(header.group(3)), "entries": set()}
            continue
        entry = re.fullmatch(r"\s+(\S+) ([0-9]+)", line)
        assert entry and current, f"unparsed listing line: {line!r}"
        listing[current]["entries"].add(entry.group(1))
    return listing


def ps_function(name: str) -> str:
    """A top-level function of the runtime library, through its column-0 brace."""
    text = RUNTIME.read_text(encoding="utf-8")
    start = re.search(rf"^function {re.escape(name)}[ ({{]", text, re.M)
    assert start, f"{name} is missing from {RUNTIME.name}"
    end = re.search(r"^}", text[start.start():], re.M)
    assert end, f"{name} has no closing brace in column 0"
    return text[start.start():start.start() + end.end()]


def dotnet_to_python(pattern: str) -> str:
    return pattern.replace("(?<", "(?P<")


def flavor_patterns() -> list[str]:
    body = ps_function("ConvertFrom-ODSNativeLlamaFlavor")
    patterns = re.findall(r"\[regex\]::Match\(\$Flavor, '([^']+)'\)", body)
    assert len(patterns) == 2, patterns
    return [dotnet_to_python(pattern) for pattern in patterns]


def test_cpu_pins_name_the_official_release_zips() -> None:
    pins = cpu_pins()
    windows_build = load_json("config/backends/amd.json")["runtime"]["llama_server"]["windows"]["release_tag"]
    assert set(pins) == {"windows", "windows_arm64"}, sorted(pins)
    for key, arch in PINS:
        pin = pins[key]
        tag, flavor = pin["release_tag"], pin["flavor"]
        assert tag == windows_build, (key, tag)
        assert flavor == f"win-cpu-{arch}" and pin["backend"] == "cpu", pin
        assert pin["policy_key"] == "windows-native", pin
        assert len(pin["archives"]) == 1, pin["archives"]
        archive = pin["archives"][0]
        assert archive["asset"] == f"llama-{tag}-bin-{flavor}.zip", archive
        assert re.fullmatch(r"[0-9a-f]{64}", archive["sha256"]), archive
        assert isinstance(archive["size"], int) and ONE_MIB <= archive["size"] <= 512 * ONE_MIB, archive
        assert "min_driver_version" not in pin and "cuda_archs_by_compute" not in pin, pin


def test_cpu_pins_match_the_measured_zips_and_their_required_files() -> None:
    listing = zip_listing()
    for key, arch in PINS:
        pin = cpu_pins()[key]
        archive = pin["archives"][0]
        measured = listing[archive["asset"]]
        assert (measured["sha256"], measured["size"]) == (archive["sha256"], archive["size"]), key
        required = pin["required_files"]
        assert required[0] == "llama-server.exe", required
        assert len({name.lower() for name in required}) == len(required), required
        assert all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}", name) for name in required), required
        assert "ggml-vulkan.dll" not in required, required
        missing = set(required) - measured["entries"]
        assert not missing, f"{key}: required files the zip does not ship: {sorted(missing)}"
        baseline = "ggml-cpu-x64.dll" if arch == "x64" else "ggml-cpu.dll"
        runtime = "libomp140.x86_64.dll" if arch == "x64" else "libomp140.aarch64.dll"
        assert {baseline, runtime, "ggml.dll", "ggml-base.dll", "llama.dll"} <= set(required), required


def test_cpu_archives_are_locked_by_sha256() -> None:
    lock = load_json("config/dependency-lock.json")
    locked = sorted(
        entry["value"] for entry in lock["entries"]
        if entry.get("type") == "archive" and entry.get("path") == "config/backends/cpu.json"
    )
    assert locked == sorted(cpu_pins()[key]["archives"][0]["sha256"] for key, _ in PINS), locked


def test_runtime_flavor_rules_accept_exactly_the_pinned_flavors() -> None:
    body = ps_function("ConvertFrom-ODSNativeLlamaFlavor")
    assert "$script:ODSNativeLlamaVulkanFlavor" in body
    patterns = flavor_patterns()

    def known(flavor: str) -> bool:
        return flavor == "win-vulkan-x64" or any(re.match(pattern, flavor) for pattern in patterns)

    for flavor in ("win-vulkan-x64", "win-cuda-13.4-arm64", "win-cpu-x64", "win-cpu-arm64"):
        assert known(flavor), flavor
    for flavor in ("win-cuda-13.4-x64", "win-cpu-x86", "win-cpu-arm64ec", "win-vulkan-arm64", "win-hip-x64"):
        assert not known(flavor), flavor
    for key, _ in PINS:
        assert known(cpu_pins()[key]["flavor"]), key
    nvidia = load_json("config/backends/nvidia.json")["runtime"]["llama_server"]["windows_arm64"]
    assert known(nvidia["flavor"]), nvidia["flavor"]


def test_runtime_reads_cpu_pins_and_keeps_the_vulkan_v1_required_files() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    assert "$script:ODSNativeLlamaRequiredFiles = @('llama-server.exe', 'ggml-vulkan.dll')" in text
    pin = ps_function("Get-ODSNativeLlamaPin")
    assert "-ContractName 'cpu.json'" in pin
    assert "'windows_arm64' } else { 'windows' }" in pin
    assert "[ValidateSet('amd', 'nvidia', 'intel', 'cpu', IgnoreCase = $false)]" in pin
    # NVIDIA and Intel reach the Vulkan pin only with -NativeApp.
    assert "$Backend -ceq 'nvidia' -and -not $NativeApp" in pin
    assert "$Backend -ceq 'intel' -and -not $NativeApp" in pin
    cleanup = ps_function("Remove-ODSNativeLlamaOldRuntimes")
    pattern = re.search(r"-notmatch '([^']+)'", cleanup).group(1)
    for key, _ in PINS:
        flavor = cpu_pins()[key]["flavor"]
        assert re.match(pattern, f"b9014-{flavor}"), flavor
    assert not re.match(pattern, "b9014-win-cpu-x86")


def test_only_the_native_app_profile_turns_the_web_ui_on() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    assert "$script:ODSNativeLlamaLaunchProfiles = @('api', 'native-app')" in text
    launch = ps_function("New-ODSNativeLlamaLaunchArguments")
    assert launch.count("'--no-webui'") == 1, launch
    assert re.search(r"if \(\$launchProfile -ceq 'api'\) \{\s+\$arguments \+= '--no-webui'", launch), launch
    assert "'--host', '127.0.0.1'" in launch and "'--api-key-file'" in launch and "'--metrics'" in launch
    assert "'--webui-config-file'" in launch and "$launchProfile -ceq 'native-app'" in launch
    profile = ps_function("Get-ODSNativeLlamaOptionsProfile")
    assert "if ($null -eq $Options.Profile) { return 'api' }" in profile
    # The CUDA generation check keeps its own --no-webui launch.
    assert "'--no-webui'" in ps_function("Invoke-ODSNativeLlamaGenerationProbe")
    # Existing launchers never ask for the native-app profile, so they keep --no-webui.
    for name in ("wsl-portal-amd.ps1", "native-llama-legacy.ps1", "portal-model-control.ps1", "spark-memory.ps1"):
        source = (ROOT / "installers/windows/lib" / name).read_text(encoding="utf-8")
        assert "native-app" not in source and not re.search(r"\bProfile\s*=", source), name


def test_powershell_contract_carries_the_same_pins() -> None:
    text = PS_CONTRACT.read_text(encoding="utf-8")
    for key, _ in PINS:
        archive = cpu_pins()[key]["archives"][0]
        assert archive["sha256"] in text and str(archive["size"]) in text and archive["asset"] in text, key
    for label in ("the native-app profile only drops --no-webui",
                  "options without a profile (the WSL Portal and legacy AMD launch) keep --no-webui",
                  "in the native app $vendor GPUs on x64 get the same Vulkan build as AMD"):
        assert label in text, label


def test_fetch_runtime_documents_its_parameters_and_uses_the_library() -> None:
    text = FETCH.read_text(encoding="utf-8")
    assert text.isascii()
    header, _, body = text.partition("[CmdletBinding()]")
    params = re.findall(r"\]\[(?:string|int|switch)\]\$(\w+)", body.split("$ErrorActionPreference", 1)[0])
    params += re.findall(r"^\s+\[(?:string|int|switch)\]\$(\w+)", body.split("$ErrorActionPreference", 1)[0], re.M)
    assert set(params) >= {"StateRoot", "Architecture", "SourceRoot", "ModelPath", "Port", "ContextSize",
                           "NativeApp", "StartupTimeoutSeconds", "StopAfterProof"}, params
    for name in set(params):
        assert f"-{name}" in header, f"fetch-runtime.ps1 does not document -{name}"
    assert "Get-ODSNativeLlamaPin -SourceRoot $SourceRoot -Backend cpu" in body
    assert "Install-ODSNativeLlamaRuntime" in body and "Test-ODSNativeLlamaQualification" in body
    assert "New-ODSNativeLlamaLaunchArguments $plan $options" in body
    assert "--ctx-size" not in body and "--host" not in body, "fetch-runtime.ps1 must not build its own llama-server argv"
