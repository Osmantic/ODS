#!/usr/bin/env python3
"""Contract checks for the cross-platform installed-footprint policy."""

from pathlib import Path
import re
import shlex


ROOT = Path(__file__).resolve().parents[1]
MACOS_INSTALLER = ROOT / "installers" / "macos" / "install-macos.sh"
WINDOWS_PHASE = ROOT / "installers" / "windows" / "phases" / "06-directories.ps1"
LINUX_BOOTSTRAP = ROOT / "get-ods.sh"

DEV_ONLY_DIRS = {"tests", "docs", "examples", ".github"}
DEV_ONLY_FILES = {
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "EDGE-QUICKSTART.md",
    "FAQ.md",
    "QUICKSTART.md",
    "SECURITY.md",
    "README.md",
    ".shellcheckrc",
    "PSScriptAnalyzerSettings.psd1",
    "test-stack.sh",
    ".gitignore",
}


def _bash_array(source: str, name: str) -> set[str]:
    match = re.search(rf"(?s)\b{re.escape(name)}=\(\s*(.*?)\)", source)
    assert match, f"missing Bash array {name}"
    return set(shlex.split(match.group(1), comments=True, posix=True))


def _powershell_array(source: str, name: str) -> set[str]:
    match = re.search(rf"(?s)\${re.escape(name)}\s*=\s*@\((.*?)\)", source)
    assert match, f"missing PowerShell array ${name}"
    return set(re.findall(r'"([^"]+)"', match.group(1)))


def main() -> None:
    macos = MACOS_INSTALLER.read_text(encoding="utf-8")
    windows = WINDOWS_PHASE.read_text(encoding="utf-8")
    linux = LINUX_BOOTSTRAP.read_text(encoding="utf-8")

    assert _bash_array(macos, "_ods_dev_only_dirs") == DEV_ONLY_DIRS
    assert _bash_array(macos, "_ods_dev_only_files") == DEV_ONLY_FILES
    assert _powershell_array(windows, "devOnlyDirectories") == DEV_ONLY_DIRS
    assert _powershell_array(windows, "devOnlyFiles") == DEV_ONLY_FILES

    # macOS patterns are anchored to the source root. Nested extension assets
    # with the same basename must remain eligible for deployment.
    assert '_ods_dev_rsync_excludes+=(--exclude="/${_ods_dev_path}/")' in macos
    assert '_ods_dev_rsync_excludes+=(--exclude="/${_ods_dev_path}")' in macos
    assert '"${_ods_dev_rsync_excludes[@]}"' in macos

    # Windows passes absolute source-root paths to robocopy for the same reason.
    assert "Join-Path $sourceRoot $_" in windows
    assert "$robocopyArgs += @($devOnlyDirectories | ForEach-Object" in windows
    assert "$robocopyArgs += @($devOnlyFiles | ForEach-Object" in windows

    # Exclusions alone do not clean upgrades. Both installers must remove stale
    # product-owned paths after a successful copy, while their surrounding
    # source != install guard protects in-place developer checkouts.
    mac_copy_guard = macos.index('if [[ "$SOURCE_ROOT" != "$INSTALL_DIR" ]]')
    mac_cleanup = macos.index('rm -rf -- "${INSTALL_DIR:?}/${_ods_dev_path}"')
    mac_in_place = macos.index('ai "Running in-place, skipping file copy"', mac_cleanup)
    assert mac_copy_guard < mac_cleanup < mac_in_place

    win_copy_guard = windows.index("if ($sourceRoot -ne $installDir)")
    win_copy_status = windows.index("if ($LASTEXITCODE -gt 7)", win_copy_guard)
    win_cleanup = windows.index("$stalePath = Join-Path $installDir $devOnlyPath")
    win_in_place = windows.index(
        'Write-AI "Running in-place (source == install directory) -- skipping file copy"',
        win_cleanup,
    )
    assert win_copy_guard < win_copy_status < win_cleanup < win_in_place
    assert "Remove-Item -LiteralPath $stalePath -Recurse -Force" in windows

    # The Linux bootstrap remains the baseline: its staged install must omit
    # the same root directories and non-Markdown developer files.
    for directory in DEV_ONLY_DIRS:
        assert f"--exclude='{directory}/'" in linux
    assert "--exclude='*.md'" in linux
    for filename in DEV_ONLY_FILES:
        if filename.endswith(".md"):
            continue
        assert f"--exclude='{filename}'" in linux

    # User/runtime state must never enter the development-only policy.
    protected = {"data", "models", "config", "extensions", ".env"}
    assert not protected & DEV_ONLY_DIRS
    assert not protected & DEV_ONLY_FILES

    print("[PASS] installed-footprint contract")


if __name__ == "__main__":
    main()
