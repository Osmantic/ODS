#!/usr/bin/env python3
"""Static contracts for the Windows-native app bundle and per-user installer.

PowerShell and Inno Setup do not run on Linux CI, so this pins what can be
read from the sources (the Windows workflow builds and runs them):

- installer/ODS.iss installs per user without elevation into
  %LOCALAPPDATA%\\Programs\\ODS, sets up and starts ODS with checked exit
  codes, and runs `ods-native.ps1 uninstall-state` on uninstall;
- no installer file refers to administrator elevation, WSL or Docker;
- every download in build-bundle.ps1 is pinned by URL and SHA-256, and the
  Python wheels come from the hash-locked requirements only;
- ods-native.ps1 keeps the API keys in owner-only files and never puts them
  on a command line, in the scheduled task, in the config or in output;
- the state paths and secret names agree with the supervisor's plan.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "installers" / "windows" / "native"
ISS = NATIVE / "installer" / "ODS.iss"
BUILD = NATIVE / "build-bundle.ps1"
CONTROL = NATIVE / "ods-native.ps1"
LIB = NATIVE / "ods-native-lib.ps1"
LAUNCHER = NATIVE / "launch-ods.vbs"

sys.path.insert(0, str(NATIVE))
from supervisor import plan  # noqa: E402  (the bundled package, imported as the app does)


def _ps_code(path: Path) -> str:
    """PowerShell source without comment lines or <# #> help blocks."""
    text = re.sub(r"<#.*?#>", "", path.read_text(encoding="utf-8"), flags=re.S)
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _iss_code() -> str:
    text = ISS.read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(";"))


def _iss_setup() -> dict[str, str]:
    section = re.search(r"^\[Setup\]\n(.*?)(?=^\[)", ISS.read_text(encoding="utf-8"), flags=re.S | re.M)
    assert section, "ODS.iss has no [Setup] section"
    pairs = (line.split("=", 1) for line in section.group(1).splitlines() if "=" in line and not line.startswith(";"))
    return {key.strip(): value.strip() for key, value in pairs}


def test_installer_is_per_user_without_elevation():
    setup = _iss_setup()
    assert setup["PrivilegesRequired"] == "lowest"
    assert "PrivilegesRequiredOverridesAllowed" not in setup, "an override would allow an all-users (admin) install"
    assert setup["DefaultDirName"] == r"{localappdata}\Programs\ODS"
    assert setup["DisableDirPage"] == "yes", "uninstall deletes {app} whole, so it must be the fixed per-user folder"
    assert re.fullmatch(r"\{\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}", setup["AppId"])
    assert setup["AppVersion"] == "{#AppVersion}"
    assert setup["ArchitecturesInstallIn64BitMode"] == "x64compatible"


def test_installer_sets_up_starts_and_uninstalls():
    code = _iss_code()
    assert re.search(r'^Source: "\{#BundleDir\}\\\*"', code, flags=re.M)
    assert re.search(r'^Name: "\{userprograms\}\\ODS";.*launch-ods\.vbs', code, flags=re.M)
    uninstall_run = re.search(r"^\[UninstallRun\]\n(.*?)(?=^\[)", code, flags=re.S | re.M)
    assert uninstall_run and "uninstall-state" in uninstall_run.group(1)
    assert "RunOnceId" in uninstall_run.group(1)
    # install-state and start are checked (Setup exits 20 on failure), not fire-and-forget [Run] entries.
    assert "RunControl('install-state')" in code and "RunControl('start')" in code
    assert "GetCustomSetupExitCode" in code
    assert "RunControl('stop')" in code, "an upgrade must stop the running supervisor before replacing files"
    assert "/REMOVEDATA" in code and "uninstall-state -RemoveData" in code
    assert re.search(r"^Type: filesandordirs; Name: \"\{app\}\"", code, flags=re.M)


def test_no_elevation_wsl_or_docker():
    sources = {
        ISS: _iss_code(),
        BUILD: _ps_code(BUILD),
        CONTROL: _ps_code(CONTROL),
        LIB: _ps_code(LIB),
        LAUNCHER: "\n".join(row for row in LAUNCHER.read_text(encoding="utf-8").splitlines() if not row.startswith("'")),
    }
    forbidden = re.compile(r"\badmin\w*|\bwsl\b|\bdocker\b|-Verb\s+RunAs|RunLevel\s+Highest|requireadministrator", re.I)
    for path, code in sources.items():
        hits = forbidden.findall(code)
        assert not hits, f"{path.name} refers to {sorted(set(hits))}"


def test_every_download_is_pinned_by_sha256():
    code = _ps_code(BUILD)
    urls = re.findall(r"https?://[^\s'\"]+", code)
    assert urls, "build-bundle.ps1 downloads nothing?"
    for url in urls:
        line = next(row for row in code.splitlines() if url in row)
        assert url.startswith("https://"), url
        assert re.search(r"Sha256 = '[0-9a-f]{64}'", line), f"{url} has no SHA-256 pin on its line"
    assert "Get-FileHash" in code and "SHA-256 mismatch" in code
    assert "Invoke-WebRequest" in code and code.count("Invoke-WebRequest") == 1, "download only through Get-PinnedDownload"


def test_python_wheels_come_from_the_hash_lock_only():
    code = _ps_code(BUILD)
    for flag in ("'--require-hashes'", "'--only-binary=:all:'", "'--no-deps'", "'--isolated'"):
        assert flag in code, flag
    assert r"dashboard-api\requirements.lock" in code
    assert "python312._pth" in code and "'..\\native'" in code
    lock = (ROOT / "extensions" / "services" / "dashboard-api" / "requirements.lock").read_text(encoding="utf-8")
    assert "--hash=sha256:" in lock


def test_bundle_has_runtime_hook_and_dashboard_build():
    code = _ps_code(BUILD)
    assert "fetch-runtime.ps1" in code and "-Destination $llama" in code
    assert "'ci'" in code and "'run', 'build'" in code
    for part in ("python\\pythonw.exe", "native\\supervisor\\__main__.py", "dashboard-api\\main.py",
                 "dashboard\\dist\\index.html", "llama\\llama-server.exe", "ods-native.ps1", "launch-ods.vbs"):
        assert part in code, part


def test_secrets_stay_in_owner_only_files():
    control = _ps_code(CONTROL)
    lib = _ps_code(LIB)
    # Created with a cryptographic RNG, written only through the private writer, ACL re-verified.
    assert "[Security.Cryptography.RandomNumberGenerator]::Create()" in control
    assert "Write-ODSPrivateEnvFile -Path $Path -Content (ConvertTo-ODSNativeSecretText -Bytes $bytes)" in control
    assert control.count("Protect-ODSPrivateEnvFile") >= 2
    # The key paths are only handed to the secret-file creator.
    for line in control.splitlines():
        if re.search(r"LlamaKey|DashboardKey|SecretsDir|\$bytes", line):
            assert re.search(r"New-ODSNativeSecretFile -Path|New-Object byte|GetBytes\(\$bytes\)|"
                             r"ConvertTo-ODSNativeSecretText -Bytes \$bytes|SecretsDir, \$script:Paths\.RunDir", line), line
    # Nothing builds an api-key argument; the supervisor passes --api-key-file itself.
    for code in (control, lib):
        assert not re.search(r"--api-key(?!-file)|api_key\s*=|API_KEY\s*=", code)
    # The task, the Startup script and the config carry only paths.
    arguments = re.search(r"function Get-ODSNativeSupervisorArguments.*?\n}", lib, flags=re.S).group(0)
    assert "ConfigPath" in arguments and "secret" not in arguments.lower()
    assert "secret" not in re.search(r"function New-ODSNativeSupervisorConfig.*?\n}", lib, flags=re.S).group(0).lower()


def test_paths_agree_with_the_supervisor_plan():
    lib = LIB.read_text(encoding="utf-8")
    for secret_name in plan.SECRET_FILES.values():
        assert f"'{secret_name}'" in lib, secret_name
    assert "-Child 'run'" in lib and "-Child 'supervisor.stop'" in lib and "-Child 'supervisor.json'" in lib
    # The supervisor rejects unknown keys; the lib writes schema 1 with loopback only.
    assert "schema_version = 1" in lib and plan.SCHEMA_VERSION == 1
    assert f"host = '{plan.LOOPBACK_HOST}'" in lib
    for key in ("enabled", "port", "model_path", "ctx_size", "exe", "webui", "extra_args"):
        assert key in plan._SERVICE_KEYS["llama-server"]
    for key in ("enabled", "port", "chat_port", "python_exe", "app_dir", "env"):
        assert key in plan._SERVICE_KEYS["dashboard-api"]


def test_supervisor_accepts_the_config_the_lib_writes():
    # Mirror of New-ODSNativeSupervisorConfig for a fixed path set; Pester
    # checks the PowerShell side produces exactly these keys.
    root = "C:\\Users\\Ann\\AppData\\Local"
    config = {
        "schema_version": 1, "install_root": root + "\\Programs\\ODS", "state_root": root + "\\ODS\\Native",
        "host": "127.0.0.1",
        "services": {
            "llama-server": {"enabled": True, "port": 8080, "model_path": "D:\\m.gguf", "ctx_size": 8192,
                             "exe": root + "\\Programs\\ODS\\llama\\llama-server.exe", "webui": True,
                             "extra_args": []},
            "dashboard-api": {"enabled": True, "port": 3002, "chat_port": 3004, "python_exe": root + "\\Programs\\ODS\\python\\python.exe",
                              "app_dir": root + "\\Programs\\ODS\\dashboard-api",
                              "env": {"SERVICE_HOST": "127.0.0.1", "GPU_BACKEND": "cpu"}},
        },
        "restart": {"delay_seconds": 5, "max_restarts": 3, "window_seconds": 300},
        "logs": {"max_bytes": 10485760, "backups": 3},
    }
    launch = plan.build_plan(config)
    assert launch.stop_file == root + "\\ODS\\Native\\run\\supervisor.stop"
    assert [s.name for s in launch.services] == ["llama-server", "dashboard-api", "chat"]
    assert "$script:ODSNativeChatPort = 3004" in LIB.read_text(encoding="utf-8")
    assert "--no-webui" not in launch.services[0].argv
    assert "webui = $true" in LIB.read_text(encoding="utf-8")
    config["services"]["llama-server"] = {"enabled": False}
    assert [s.name for s in plan.build_plan(config).services] == ["dashboard-api"]


def test_start_menu_launcher_is_hidden():
    text = LAUNCHER.read_text(encoding="utf-8")
    assert 'ods-native.ps1"" open -ShowErrors", 0, False' in text
    assert "-ExecutionPolicy Bypass" in text
