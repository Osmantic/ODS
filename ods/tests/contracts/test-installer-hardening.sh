#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

assert_contains() {
  local file="$1"
  local pattern="$2"
  local msg="$3"
  if ! grep -qE -- "$pattern" "$file"; then
    echo "[FAIL] $msg"
    echo "---- output ----"
    cat "$file"
    echo "----------------"
    exit 1
  fi
}

assert_not_contains() {
  local file="$1"
  local pattern="$2"
  local msg="$3"
  if grep -qE -- "$pattern" "$file"; then
    echo "[FAIL] $msg"
    echo "---- output ----"
    cat "$file"
    echo "----------------"
    exit 1
  fi
}

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

echo "[contract] resolve-compose-stack fails loud when PyYAML is missing"
fake_py="$tmpdir/python3"
cat > "$fake_py" <<'PYEOF'
#!/usr/bin/env bash
code=""
if [[ "${1:-}" == "-c" ]]; then
  code="${2:-}"
fi
case "$code" in
  *"import sys"*) exit 0 ;;
  *"import yaml"*) echo "ModuleNotFoundError: No module named 'yaml'" >&2; exit 1 ;;
esac
echo "unexpected fake python invocation: $*" >&2
exit 99
PYEOF
chmod +x "$fake_py"
cp "$fake_py" "$tmpdir/python"

missing_yaml_err="$tmpdir/missing-yaml.err"
if PATH="$tmpdir:$PATH" USERPROFILE="" LOCALAPPDATA="" ODS_PYTHON_CMD="$fake_py" scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" >"$tmpdir/missing-yaml.out" 2>"$missing_yaml_err"; then
  echo "[FAIL] resolver should fail when selected Python cannot import yaml"
  exit 1
fi
assert_contains "$missing_yaml_err" 'PyYAML is required' "resolver did not explain PyYAML requirement"
assert_contains "$missing_yaml_err" 'conda deactivate' "resolver did not include Conda/venv recovery hint"

echo "[contract] Linux Python guard installs a missing interpreter before PyYAML"
runtime_out="$tmpdir/python-runtime.out"
runtime_calls="$tmpdir/python-runtime.calls"
bash -c '
  set -euo pipefail
  ROOT_DIR="$1"
  tmpdir="$2"
  calls="$3"
  cd "$ROOT_DIR"

  SCRIPT_DIR="$ROOT_DIR"
  LOG_FILE=/dev/null
  DRY_RUN=false
  INTERACTIVE=false
  PKG_MANAGER=apt

  ai() { echo "AI $*"; }
  ai_ok() { echo "OK $*"; }
  ai_warn() { echo "WARN $*"; }
  ai_bad() { echo "BAD $*"; }
  error() { echo "ERROR $*" >&2; exit 1; }
  pkg_update() { echo "pkg_update" >>"$calls"; }
  pkg_install() {
    local pkg
    for pkg in "$@"; do
      echo "pkg_install:$pkg" >>"$calls"
      case "$pkg" in
        python3) touch "$tmpdir/python-ready" ;;
        python3-pyyaml|python3-yaml|pyyaml) touch "$tmpdir/yaml-ready" ;;
      esac
    done
  }
  pkg_resolve() { echo "$1"; }

  fake_py="$tmpdir/fake-python3"
  cat > "$fake_py" <<PYEOF
#!/usr/bin/env bash
code=""
if [[ "\${1:-}" == "-c" ]]; then
  code="\${2:-}"
fi
case "\$code" in
  *"import sys"*) exit 0 ;;
  *"import yaml"*) [[ -f "$tmpdir/yaml-ready" ]] && exit 0 || exit 1 ;;
esac
exit 0
PYEOF
  chmod +x "$fake_py"

  source installers/lib/python-runtime.sh
  ods_detect_python_cmd() {
    [[ -f "$tmpdir/python-ready" ]] || return 1
    printf "%s" "$fake_py"
  }

  ods_ensure_python_module yaml python3-pyyaml pyyaml PyYAML
' bash "$ROOT_DIR" "$tmpdir" "$runtime_calls" >"$runtime_out"
assert_contains "$runtime_calls" 'pkg_update' "Python guard did not update package metadata before installing"
assert_contains "$runtime_calls" 'pkg_install:python3' "Python guard did not install missing python3"
assert_contains "$runtime_calls" 'pkg_install:python3-pyyaml' "Python guard did not install PyYAML after python3"
assert_contains "$runtime_out" 'OK PyYAML available' "Python guard did not re-check PyYAML after install"

echo "[contract] public bootstrap supports non-gnu Linux OSTYPE and zypper prerequisites"
bootstrap="get-ods.sh"
assert_contains "$bootstrap" '\$\{OSTYPE:-\}' "bootstrap should guard OSTYPE when detecting Linux"
assert_contains "$bootstrap" '== linux\*' "bootstrap should treat openSUSE/Tumbleweed linux variants as Linux"
assert_contains "$bootstrap" 'command -v zypper' "bootstrap missing zypper package-manager branch"
assert_contains "$bootstrap" 'zypper --non-interactive install -y git' "bootstrap cannot install git on zypper distros"
assert_contains "$bootstrap" 'zypper --non-interactive install -y curl' "bootstrap cannot install curl on zypper distros"
assert_contains "$bootstrap" 'ODS_REF' "bootstrap should allow PR/fleet lanes to clone a matching ref"
assert_contains "$bootstrap" 'clone_args\+=\(--branch "\$ODS_REF"\)' "bootstrap ref override should apply to git clone"
assert_contains "$bootstrap" 'ods_ref_is_exact_sha' "bootstrap should detect exact commit SHA refs"
assert_contains "$bootstrap" 'checkout_requested_sha_ref "\$ODS_REF"' "bootstrap should checkout exact SHA refs after cloning"
assert_contains "$bootstrap" 'BOOTSTRAP_FORCE=false' "bootstrap should parse --force before incomplete install prompts"
assert_contains "$bootstrap" 'BOOTSTRAP_NON_INTERACTIVE=false' "bootstrap should parse --non-interactive before incomplete install prompts"
assert_contains "$bootstrap" 'Removing incomplete install because --force was provided' "bootstrap --force should remove incomplete install dirs without prompting"
assert_contains "$bootstrap" 'Re-run with --force to remove it automatically' "bootstrap --non-interactive should fail with a force hint instead of prompting"
assert_contains "$bootstrap" 'remove_install_dir()' "bootstrap should centralize incomplete install cleanup"
assert_contains "$bootstrap" 'sudo -n rm -rf -- "\$target_dir"' "bootstrap --force should retry root-owned container data cleanup with sudo -n"
assert_contains "$bootstrap" 'root-owned container data' "bootstrap sudo fallback should explain root-owned Docker data cleanup"

echo "[contract] public bootstrap can install from an exact commit SHA"
sha_repo="$tmpdir/sha-ref-repo"
sha_home="$tmpdir/sha-home"
sha_install="$tmpdir/sha-install"
sha_marker="$tmpdir/sha-marker"
mkdir -p "$sha_repo/ods/scripts" "$sha_repo/ods/extensions/library" "$sha_home" "$tmpdir/bin"
cat > "$sha_repo/ods/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' first-commit > "${ODS_TEST_BOOTSTRAP_INSTALL_MARKER:?}"
EOF
chmod +x "$sha_repo/ods/install.sh"
git -C "$sha_repo" init -q
git -C "$sha_repo" add ods
git -C "$sha_repo" \
  -c user.name="ODS Test" \
  -c user.email="ods-test@example.invalid" \
  commit -q -m "first install payload"
sha_ref="$(git -C "$sha_repo" rev-parse HEAD)"
cat > "$sha_repo/ods/install.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' second-commit > "${ODS_TEST_BOOTSTRAP_INSTALL_MARKER:?}"
EOF
git -C "$sha_repo" add ods/install.sh
git -C "$sha_repo" \
  -c user.name="ODS Test" \
  -c user.email="ods-test@example.invalid" \
  commit -q -m "second install payload"

cat > "$tmpdir/bin/docker" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
  --version) printf '%s\n' "Docker version test"; exit 0 ;;
  compose|ps) exit 0 ;;
esac
exit 0
EOF
chmod +x "$tmpdir/bin/docker"

if ! PATH="$tmpdir/bin:$PATH" \
    HOME="$sha_home" \
    ODS_BOOTSTRAP_ROOT="$sha_home" \
    ODS_REPO_URL="file://$sha_repo" \
    ODS_REF="$sha_ref" \
    ODS_INSTALL_DIR="$sha_install" \
    ODS_ALLOW_LEGACY_PARALLEL=1 \
    ODS_TEST_BOOTSTRAP_INSTALL_MARKER="$sha_marker" \
    bash get-ods.sh --non-interactive >"$tmpdir/bootstrap-sha.out" 2>&1; then
  cat "$tmpdir/bootstrap-sha.out"
  echo "[FAIL] bootstrap exact-SHA install failed"
  exit 1
fi
grep -qF first-commit "$sha_marker" \
  || { cat "$tmpdir/bootstrap-sha.out"; echo "[FAIL] bootstrap did not install the exact SHA payload"; exit 1; }
assert_not_contains "$tmpdir/bootstrap-sha.out" 'Remote branch .* not found' "bootstrap treated an exact SHA as a branch name"

echo "[contract] runtime dispatcher supports non-gnu Linux OSTYPE"
dispatcher_common="installers/common.sh"
assert_contains "$dispatcher_common" '\$\{OSTYPE:-\}' "dispatcher should guard OSTYPE when detecting Linux"
assert_contains "$dispatcher_common" '== linux\*' "dispatcher should treat openSUSE/Tumbleweed linux variants as Linux"

echo "[contract] install-core loads service registry after Python prerequisites"
order_out="$tmpdir/install-core-order.out"
python3 - "$ROOT_DIR/install-core.sh" >"$order_out" <<'PY'
import sys
from pathlib import Path

lines = Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
source_idx = next(i for i, line in enumerate(lines) if 'source "$SCRIPT_DIR/lib/service-registry.sh"' in line)
ensure_idx = next(i for i, line in enumerate(lines) if "ods_ensure_python_module yaml" in line)
load_idx = next(i for i, line in enumerate(lines) if "sr_load" in line and i > ensure_idx)
early_loads = [
    i for i, line in enumerate(lines)
    if "sr_load" in line and source_idx < i < ensure_idx
]
if early_loads:
    raise SystemExit("sr_load runs before Python prerequisites")
if not (source_idx < ensure_idx < load_idx):
    raise SystemExit("unexpected service-registry/Python prerequisite order")
print("registry-load-after-python")
PY
assert_contains "$order_out" 'registry-load-after-python' "install-core does not defer service registry load until after Python prerequisites"

echo "[contract] macOS PyYAML install uses private installer venv"
macos_installer="installers/macos/install-macos.sh"
assert_contains "$macos_installer" '_ensure_macos_pyyaml' "macOS installer missing PyYAML helper"
assert_contains "$macos_installer" 'python-cmd.sh' "macOS installer does not load python command resolver"
assert_contains "$macos_installer" '\.venv/installer-python' "macOS installer missing private venv runtime"
assert_contains "$macos_installer" '\$pycmd" -m venv "\$venv_dir"' "macOS installer does not create the venv with the selected Python"
assert_not_contains "$macos_installer" 'pip install --user .*pyyaml|pip install .*--user .*pyyaml' "macOS installer still tries user-site PyYAML first"
assert_contains "$macos_installer" 'export ODS_PYTHON_CMD' "macOS installer does not export selected Python"
assert_contains "$macos_installer" '_ods_python_cmd_cached=' "macOS installer does not refresh python resolver cache"

echo "[contract] macOS bootstrap model download tolerates slow resumable transfers"
macos_ui="installers/macos/lib/ui.sh"
assert_contains "$macos_ui" 'curl -C - -L --progress-bar' "macOS bootstrap model download should preserve curl resume support"
assert_contains "$macos_ui" 'ODS_DOWNLOAD_CONNECT_TIMEOUT:-30' "macOS bootstrap model download should allow configurable connect timeout"
assert_contains "$macos_ui" 'ODS_DOWNLOAD_LOW_SPEED_TIME:-300' "macOS bootstrap model download should tolerate slow but active transfers"
assert_contains "$macos_ui" 'ODS_DOWNLOAD_LOW_SPEED_LIMIT:-1024' "macOS bootstrap model download should use a lenient low-speed threshold"
assert_not_contains "$macos_ui" '--speed-time 30 --speed-limit 10240' "macOS bootstrap model download should not abort active slow transfers after 30 seconds"

echo "[contract] Windows bootstrap model download uses retry wrapper"
win_installer="installers/windows/install-windows.ps1"
win_lemonade_helper="installers/windows/lib/backend-contract.ps1"
assert_contains "$win_installer" 'Invoke-DownloadWithRetry -Url \$tierConfig\.GgufUrl' "Windows installer should retry/resume transient GGUF download failures"
assert_not_contains "$win_installer" '\$dlOk = Show-ProgressDownload -Url \$tierConfig\.GgufUrl' "Windows installer bootstrap model path should not bypass retry wrapper"
assert_contains "$win_installer" 'Get-ODSLemonadeLaunchContract' "Windows installer should select Lemonade arguments by executable version"
assert_contains "$win_installer" 'New-ODSLemonadeScheduledTaskAction' "Windows installer should launch Lemonade through the shared task contract"
assert_contains "$win_installer" 'Start-ODSLemonadeDirectProcess' "Windows installer should use the shared direct-launch fallback"
assert_contains "$win_installer" 'Set-ODSLemonadeModernRuntimeConfig' "Windows installer should configure and verify Lemonade 10.7 after startup"
assert_contains "$win_installer" 'Format-ODSLemonadeLaunchDiagnostics' "Windows installer should report child/task/log diagnostics before fallback"
assert_not_contains "$win_installer" 'serve --port .*--no-tray .*--llamacpp .*--extra-models-dir' "Windows installer must not hard-code obsolete Lemonade arguments"
assert_contains "$win_lemonade_helper" 'extra_models_dir = ' "Windows Lemonade helper should post the 10.7 extra_models_dir key"
assert_contains "$win_lemonade_helper" 'llamacpp = \[ordered\]@\{' "Windows Lemonade helper should post the 10.7 nested llama.cpp config"
assert_contains "$win_lemonade_helper" 'backend = "vulkan"' "Windows Lemonade helper should request the Vulkan backend"
assert_contains "$win_lemonade_helper" 'Authorization.*Bearer' "Windows Lemonade helper should authenticate internal configuration"
assert_contains "$win_installer" 'Lemonade scheduled task did not start a server process' "Windows installer should recover when Task Scheduler reports success without a Lemonade process"
assert_contains "$win_installer" 'Start-Process msiexec\.exe .* -PassThru' "Windows installer should capture Lemonade MSI exit codes"
assert_contains "$win_installer" 'Lemonade MSI exited with code' "Windows installer should report failed Lemonade MSI exit codes honestly"
assert_contains "$win_installer" 'INSTALLDIR=' "Windows installer should install Lemonade into the normal user's runtime directory"
assert_contains "$win_installer" '/L\*V' "Windows installer should retain a verbose Lemonade MSI log for support"
assert_not_contains "$win_installer" 'ALLUSERS=1' "Windows installer must not require an elevated all-users Lemonade MSI install"
assert_contains "$win_installer" '\$_managedBin = if \(\$_resolvedExe\)' "Windows installer should scope Lemonade cleanup to the resolved ODS runtime directory"
assert_not_contains "$win_installer" '\$_knownNames -contains \$_name' "Windows installer must not stop unrelated Lemonade processes by executable name alone"
assert_contains "installers/windows/lib/backend-contract.ps1" 'Get-ODSLemonadeExeCandidatePaths' "Windows Lemonade resolver should expose candidate paths for diagnostics"
assert_contains "installers/windows/lib/backend-contract.ps1" 'Get-ODSLemonadeUserInstallDir' "Windows Lemonade resolver should support the per-user MSI location"
assert_contains "installers/windows/lib/backend-contract.ps1" 'LOCALAPPDATA' "Windows Lemonade resolver should probe the current user's AppData location"
assert_contains "$win_installer" 'Get-ODSWindowsUserDockerClientArgs' "Windows Docker fallback should preserve an existing user Docker config"
assert_contains "$win_installer" 'image validation failed with the install-scoped Docker config' "Windows Docker fallback should cover image validation before builds"
assert_contains "$win_installer" 'Continuing Compose preflight and service launch with the user'\''s Docker config' "Windows Docker fallback should carry through Compose preflight and launch"
assert_contains "$win_installer" 'Compose service launch failed with the install-scoped Docker config' "Windows Docker fallback should retry compose up"
assert_contains "$win_installer" 'Managed-container inspection failed with the install-scoped Docker config' "Windows Docker fallback should retry managed-container inspection"
assert_contains "$win_installer" 'ODSLemonadeRuntime' "Windows installer should use a stable Lemonade scheduled task name"
assert_contains "$win_installer" 'Invoke-WindowsSttModelDownloadTrigger' "Windows installer should trigger STT preload through a bounded helper"
assert_contains "$win_installer" '--max-time 30 -X POST' "Windows installer STT preload should use a bounded curl trigger"
assert_contains "$win_installer" 'Wait-WindowsSttModelCached -ModelUrl' "Windows installer should poll STT cache readiness after triggering download"
assert_not_contains "$win_installer" 'Invoke-WebRequest -Method POST -Uri "\$whisperUrl/v1/models/\$sttModelEncoded" -TimeoutSec 600' "Windows installer should not block on the long STT preload POST"
assert_contains "$win_installer" 'exec bash "\$bashScript"' "Windows model-upgrade task should own the real full-model upgrade process"
assert_not_contains "$win_installer" 'nohup bash "\$bashScript"' "Windows model-upgrade task should not report success before the full-model upgrade exits"
assert_not_contains "$win_installer" 'disown "\$pid"' "Windows model-upgrade task should not orphan the full-model upgrade process"
assert_contains "$win_installer" '< /dev/null' "Windows installer full-model upgrade should close stdin"
assert_contains "$win_installer" 'model-upgrade.pid' "Windows installer should record the background model-upgrade PID"
assert_contains "$win_installer" 'ODSModelUpgrade' "Windows installer should launch full-model upgrade through a separate scheduled task"
assert_contains "$win_installer" 'ODSNativeLlamaRuntime' "Windows installer should launch native llama-server through a managed scheduled task"
python3 - "$win_installer" >"$tmpdir/windows-upgrade-launcher.out" <<'PY'
import sys
import re
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index('$upgradeTaskName = "ODSModelUpgrade"')
end = text.index("if (Test-Path -LiteralPath $upgradePidFile)", start)
block = text[start:end]
if re.search(r"Start-Process\s+-FilePath\s+\$bashPath[^\r\n]*\s-Wait(?:\s|$)", block):
    raise SystemExit("model upgrade launcher stays in the installer process tree")
if 'nohup bash "$bashScript"' in block or 'disown "$pid"' in block:
    raise SystemExit("model upgrade task detaches from the process it is meant to supervise")
for needle in (
    "New-ScheduledTaskAction -Execute $bashPath",
    "$upgradeSettings = New-ScheduledTaskSettingsSet",
    "-AllowStartIfOnBatteries",
    "-DontStopIfGoingOnBatteries",
    "-StartWhenAvailable",
    "-ExecutionTimeLimit ([TimeSpan]::Zero)",
    "Register-ScheduledTask -TaskName $upgradeTaskName",
    "-Settings $upgradeSettings",
    "Start-ScheduledTask -TaskName $upgradeTaskName",
    "-RunLevel Limited",
):
    if needle not in block:
        raise SystemExit(f"model upgrade launcher missing {needle}")
if "while ($scheduled -and -not (Test-Path -LiteralPath $upgradePidFile)" not in text:
    raise SystemExit("model upgrade launcher does not poll for PID handoff")
wrapper_start = text.index('$wrapperContent = @"')
wrapper_end = text.index('"@', wrapper_start)
wrapper = text[wrapper_start:wrapper_end]
if 'echo "$`$"' not in wrapper and 'echo "`$`$"' not in wrapper:
    raise SystemExit("model upgrade wrapper does not record its supervising PID")
if 'exec bash "$bashScript"' not in wrapper:
    raise SystemExit("model upgrade wrapper does not exec the real upgrade")
print("windows-upgrade-launcher-supervised")
PY

python3 - "$win_installer" >"$tmpdir/windows-native-llama-task.out" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
start = text.index('$nativeLlamaTaskName = "ODSNativeLlamaRuntime"')
end = text.index('Write-AI "Waiting for llama-server to load model..."', start)
block = text[start:end]
for needle in (
    "New-ScheduledTaskAction",
    "-Execute $script:LLAMA_SERVER_EXE",
    "$nativeLlamaPrincipal = New-ScheduledTaskPrincipal",
    "-RunLevel Limited",
    "Register-ScheduledTask -TaskName $nativeLlamaTaskName",
    "Start-ScheduledTask -TaskName $nativeLlamaTaskName",
    "Get-CimInstance Win32_Process",
):
    if needle not in block:
        raise SystemExit(f"native llama runtime task missing {needle}")
if "Start-Process -FilePath $script:LLAMA_SERVER_EXE" in block:
    raise SystemExit("elevated installer still launches native llama-server directly")
print("windows-native-llama-runtime-limited")
PY
assert_contains "$tmpdir/windows-upgrade-launcher.out" 'windows-upgrade-launcher-supervised' "Windows installer should supervise the full-model upgrade in the scheduled task"
win_phase04="installers/windows/phases/04-requirements.ps1"
assert_contains "$win_phase04" 'function Stop-WindowsODSLemonadePortConflicts' "Windows requirements phase should stop native Lemonade conflicts"
assert_contains "$win_phase04" 'Native Lemonade is running but this install uses Docker-backed inference' "Windows requirements phase should explain non-AMD Lemonade conflicts"
assert_contains "$win_phase04" '\$gpuInfo\.Backend -eq "amd" -and -not \$cloudMode' "Windows requirements phase should preserve AMD/Lemonade native runtime"
assert_contains "$win_phase04" 'Stop-Process -Id \(\[int\]\$_proc\.ProcessId\)' "Windows requirements phase should stop detected Lemonade processes"
assert_contains "$win_phase04" 'Stop-WindowsODSLemonadePortConflicts `' "Windows requirements phase should run Lemonade cleanup before port scan"
assert_contains "installers/windows/ods.ps1" 'Invoke-ODSSttModelDownloadTrigger' "ods.ps1 repair voice should trigger STT preload through a bounded helper"
assert_not_contains "installers/windows/ods.ps1" 'Invoke-WebRequest -Method POST -Uri \$voice\.SttModelUrl -TimeoutSec 3600' "ods.ps1 repair voice should not block on the long STT preload POST"
assert_contains "installers/windows/ods.ps1" 'Start-ODSLemonadeDirectProcess -Contract \$launchContract' "ods.ps1 should use the shared direct Lemonade fallback"
assert_contains "installers/windows/ods.ps1" 'Set-ODSLemonadeModernRuntimeConfig' "ods.ps1 should configure Lemonade 10.7 after health"
assert_not_contains "installers/windows/ods.ps1" 'serve --port .*--no-tray .*--llamacpp .*--extra-models-dir' "ods.ps1 must not hard-code obsolete Lemonade arguments"
assert_contains "installers/windows/ods.ps1" 'Sync-ODSNativeInferenceConfig' "ods.ps1 should sync native runtime config from .env"
assert_contains "installers/windows/ods.ps1" 'AMD_INFERENCE_PORT' "ods.ps1 should honor configured AMD Lemonade port"
assert_contains "installers/windows/ods.ps1" 'LEMONADE_HEALTH_URL = "http://localhost:\$\(\$script:LEMONADE_PORT\)/api/v1/health"' "ods.ps1 should health-check the configured Lemonade port"

echo "[contract] Windows Lemonade dashboard activation uses native runtime health"
host_agent="bin/ods-host-agent.py"
assert_contains "$host_agent" '_is_windows_host_lemonade' "host-agent missing Windows host-backed Lemonade detection"
assert_contains "$host_agent" '_restart_windows_lemonade\(env\)' "host-agent should restart Windows Lemonade through the native runtime path"
assert_contains "$host_agent" 'AMD_INFERENCE_PORT' "host-agent should health-check Windows Lemonade on AMD_INFERENCE_PORT"
assert_contains "$host_agent" 'ODSLemonadeRuntime' "host-agent should launch Windows Lemonade through Task Scheduler"
assert_contains "$host_agent" '\$existingTask = Get-ScheduledTask -TaskName \$taskName' "host-agent should reuse an existing Windows Lemonade task when running with limited privileges"
assert_contains "$host_agent" 'Could not refresh Lemonade scheduled task; reusing existing task' "host-agent should refresh stale Lemonade scheduled tasks when allowed"
assert_contains "$host_agent" 'LemonadeServer.exe' "host-agent should accept current Lemonade MSI executable aliases"
assert_contains "$host_agent" 'Start-ODSLemonadeDirectProcess -Contract \$launchContract' "host-agent should use the shared direct Lemonade fallback"
assert_contains "$host_agent" 'Set-ODSLemonadeModernRuntimeConfig' "host-agent should configure and verify Lemonade 10.7"
assert_contains "$host_agent" '\$existingTaskMatches' "host-agent should not reuse a stale Lemonade task contract"
assert_not_contains "$host_agent" '\$argString = "serve --port .*--no-tray' "host-agent must not embed obsolete Lemonade 10.7 arguments"

echo "[contract] Windows Lemonade Hermes uses LiteLLM compact path"
phase06_win="installers/windows/phases/06-directories.ps1"
assert_contains "$phase06_win" 'http://litellm:4000/v1' "Windows AMD Hermes should route through LiteLLM, not direct Lemonade"
assert_contains "$phase06_win" 'local-lemonade' "Windows AMD Hermes should render compact local profile"
assert_contains "$phase06_win" 'disabled_toolsets:' "Windows AMD Hermes should compact optional toolsets"
assert_contains "$phase06_win" 'extensions-library-bundle\\services' "Windows installer should consider public-bootstrap extensions-library bundle"
assert_contains "$phase06_win" 'extensions\\library\\services' "Windows installer should copy product extensions library templates"
assert_contains "$phase06_win" 'data/extensions-library' "Windows installer should populate data/extensions-library for dashboard extension installs"
assert_contains "scripts/build-installation-context.py" 'local-lemonade' "SOUL builder should expose local-lemonade profile"
assert_contains "extensions/services/dashboard-api/routers/models.py" '_loaded_model_backend_ready_sync' "dashboard model no-op should verify live backend readiness"

echo "[contract] Linux phase 06 reports substeps on failure"
phase06="installers/phases/06-directories.sh"
assert_contains "$phase06" 'export INSTALL_PHASE="06-directories/\$\{step\}"' "phase 06 missing substep INSTALL_PHASE updates"
for step in create-directories copy-source copy-extensions-library generate-env validate-env generate-searxng-config; do
  assert_contains "$phase06" "_phase06_step \"$step\"" "phase 06 missing substep: $step"
done

echo "[contract] Windows phase 06 stages the extension library"
win_phase06="installers/windows/phases/06-directories.ps1"
assert_contains "$win_phase06" 'extensions\\library\\services' "Windows phase 06 does not search the source extension library"
assert_contains "$win_phase06" 'data\\extensions-library' "Windows phase 06 does not stage data/extensions-library"
assert_contains "$win_phase06" 'Extensions library copied to data/extensions-library' "Windows phase 06 does not report extension library copy success"
assert_contains "$win_phase06" 'extensions\\services\\hermes-proxy\\Caddyfile' "Windows phase 06 should clean malformed Hermes proxy Caddyfile directories before compose"
assert_contains "$win_phase06" 'extensions\\services\\ods-proxy\\Caddyfile' "Windows phase 06 should clean malformed ODS proxy Caddyfile directories before compose"
assert_contains "$win_phase06" 'extensions\\services\\whisper\\docker-entrypoint.sh' "Windows phase 06 should clean malformed Whisper entrypoint directories before compose"
assert_contains "$win_phase06" 'extensions\\services\\perplexica\\docker-entrypoint.sh' "Windows phase 06 should clean malformed Perplexica entrypoint directories before compose"

echo "[contract] Python resolver can select a module-capable fallback"
pybin="$tmpdir/python-module-fallback"
mkdir -p "$pybin"
cat > "$pybin/python3" <<'PY3'
#!/usr/bin/env bash
# Runnable, but cannot import yaml.
if [[ "${1:-}" == "-c" ]]; then
  exit 0
fi
if [[ "${1:-}" == "-" && "${2:-}" == "yaml" ]]; then
  exit 1
fi
exit 0
PY3
cat > "$pybin/python" <<'PY'
#!/usr/bin/env bash
exit 0
PY
chmod +x "$pybin/python3" "$pybin/python"
module_py="$(PATH="$pybin:$PATH" bash -c '
  set -euo pipefail
  source lib/python-cmd.sh
  ods_detect_python_cmd_with_module yaml
')"
if [[ "$module_py" != "python" ]]; then
  echo "[FAIL] Python resolver did not fall back to module-capable python (got: $module_py)"
  exit 1
fi

echo "[contract] ods_sudo uses sudo -n in non-interactive mode"
fakebin="$tmpdir/fakebin"
mkdir -p "$fakebin"
cat > "$fakebin/sudo" <<'SUDOEOT'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$ODS_TEST_SUDO_ARGS"
exit 0
SUDOEOT
chmod +x "$fakebin/sudo"
sudo_args="$tmpdir/sudo.args"
PATH="$fakebin:$PATH" ODS_TEST_SUDO_ARGS="$sudo_args" bash -c '
  set -euo pipefail
  INTERACTIVE=false
  DRY_RUN=false
  source installers/lib/sudo.sh
  ods_sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
'
assert_contains "$sudo_args" '^-n nvidia-ctk cdi generate' "ods_sudo did not use sudo -n for non-interactive runs"

echo "[contract] Blackwell proprietary module is blocked"
blackwell_bin="$tmpdir/blackwell-proprietary"
mkdir -p "$blackwell_bin"
cat > "$blackwell_bin/nvidia-smi" <<'NVSMI'
#!/usr/bin/env bash
if [[ "$*" == *"--query-gpu=name,compute_cap"* ]]; then
  echo "NVIDIA RTX PRO 6000 Blackwell Workstation Edition, 12.0"
  exit 0
fi
exit 0
NVSMI
cat > "$blackwell_bin/modinfo" <<'MODINFO'
#!/usr/bin/env bash
if [[ "$*" == "-F license nvidia" ]]; then
  echo "NVIDIA"
  exit 0
fi
exit 1
MODINFO
cat > "$blackwell_bin/lspci" <<'LSPCI'
#!/usr/bin/env bash
echo "01:00.0 VGA compatible controller: NVIDIA Corporation Blackwell"
LSPCI
chmod +x "$blackwell_bin/nvidia-smi" "$blackwell_bin/modinfo" "$blackwell_bin/lspci"

if PATH="$blackwell_bin:$PATH" bash -c '
  set -euo pipefail
  LOG_FILE=/dev/null
  ai() { :; }
  ai_ok() { :; }
  ai_warn() { :; }
  ai_bad() { :; }
  error() { echo "$1"; return 42; }
  source installers/lib/detection.sh
  validate_nvidia_blackwell_open_modules
' >"$tmpdir/blackwell-proprietary.out" 2>"$tmpdir/blackwell-proprietary.err"; then
  echo "[FAIL] proprietary Blackwell module should block install"
  exit 1
fi
assert_contains "$tmpdir/blackwell-proprietary.out" 'Blackwell requires NVIDIA open kernel modules' "Blackwell proprietary failure did not explain open-module requirement"

echo "[contract] Blackwell open module is accepted"
open_bin="$tmpdir/blackwell-open"
mkdir -p "$open_bin"
cp "$blackwell_bin/nvidia-smi" "$open_bin/nvidia-smi"
cp "$blackwell_bin/lspci" "$open_bin/lspci"
cat > "$open_bin/modinfo" <<'OPENMOD'
#!/usr/bin/env bash
if [[ "$*" == "-F license nvidia" ]]; then
  echo "Dual MIT/GPL"
  exit 0
fi
exit 1
OPENMOD
chmod +x "$open_bin/nvidia-smi" "$open_bin/lspci" "$open_bin/modinfo"

PATH="$open_bin:$PATH" bash -c '
  set -euo pipefail
  LOG_FILE=/dev/null
  ai() { :; }
  ai_ok() { :; }
  ai_warn() { :; }
  ai_bad() { :; }
  error() { echo "$1"; return 42; }
  source installers/lib/detection.sh
  validate_nvidia_blackwell_open_modules
'

echo "[contract] catalog selector output is parsed without eval"
assert_contains "lib/safe-env.sh" 'load_model_selector_env_from_output' "safe env loader missing model selector allowlist"
assert_contains "scripts/select-model.py" 'return f' "model selector no longer emits parser-friendly quoted values"
if grep -q 'import shlex' scripts/select-model.py; then
  echo "[FAIL] model selector should not require shell quoting for installer consumption"
  exit 1
fi
for f in installers/phases/02-detection.sh installers/macos/install-macos.sh tests/test-tier-map.sh; do
  if grep -q 'eval "$_selector_env"' "$f"; then
    echo "[FAIL] $f still evals model selector output"
    exit 1
  fi
  assert_contains "$f" 'load_model_selector_env_from_output' "$f does not use the allowlisted selector loader"
done

echo "[contract] compose launch cannot silently produce zero containers"
assert_contains "installers/phases/11-services.sh" 'logs/compose-launch\.txt' "Linux installer missing compose launch record"
assert_contains "installers/phases/11-services.sh" 'ps -q' "Linux installer does not count compose-managed containers"
assert_contains "installers/phases/11-services.sh" 'Docker Compose did not create any managed containers' "Linux installer does not fail loud on zero managed containers"
assert_not_contains "installers/phases/11-services.sh" '_phase11_assert_managed_containers false' "Linux zero-container path must write a compose failure report"
assert_contains "installers/phases/11-services.sh" '_phase11_compose_failure_is_delayed_health' "Linux installer does not distinguish delayed health from generic compose failure"
assert_contains "installers/phases/11-services.sh" 'dependency failed to start: container ods-\(llama-server\|llama-ready\|llama-server-ready\) is unhealthy' "Linux delayed-health grace is not scoped to LLM health-gate failures"
assert_contains "installers/phases/11-services.sh" '_compose_started_with_delayed_health=true' "Linux installer does not continue after delayed compose health with managed containers"
assert_contains "installers/phases/11-services.sh" 'COMPOSE_STARTED_WITH_DELAYED_HEALTH=true' "Linux installer does not mark delayed compose health for strict phase 12 recovery"
assert_contains "installers/phases/11-services.sh" 'Continuing to the longer health checks' "Linux installer no longer delegates delayed service health to phase 12"
assert_contains "installers/phases/12-health.sh" 'COMPOSE_STARTED_WITH_DELAYED_HEALTH' "Linux delayed compose health does not make phase 12 strict"
assert_contains "installers/phases/12-health.sh" 'ODS_LLM_DELAYED_HEALTH_ATTEMPTS:-300' "Linux delayed compose health does not extend the LLM health wait for large GGUF reloads"
assert_contains "installers/phases/12-health.sh" 'Docker Compose reported delayed LLM health' "Linux strict delayed-health recovery message missing"
assert_contains "installers/phases/12-health.sh" 'bootstrap-status.json' "Phase 12 does not check bootstrap model download status"
assert_contains "installers/phases/12-health.sh" '_model_download_active' "Phase 12 does not skip health wait when model upgrade is in progress"
assert_contains "installers/phases/12-health.sh" 'verifying|swapping' "Phase 12 does not treat model verify/swap as active upgrade work"
assert_contains "installers/phases/12-health.sh" 'bg_task_status "full-model-download"' "Phase 12 does not check the running model-upgrade task"
assert_contains "scripts/bootstrap-upgrade.sh" 'write_status "swapping"' "Bootstrap upgrade does not mark the hot-swap phase active"
assert_contains "installers/phases/12-health.sh" 'EMBEDDINGS_HEALTH_FAILED=true' "Phase 12 does not mark embeddings health failure"
assert_contains "installers/phases/12-health.sh" 'Embeddings/RAG was selected' "Phase 12 does not explain embeddings/RAG health failure"
assert_contains "installers/phases/12-health.sh" 'SERVICE_PORTS\[embeddings\]:-8090' "Phase 12 embeddings fallback port must match manifest default"
assert_contains "extensions/services/embeddings/compose.yaml" 'HF_HUB_DOWNLOAD_TIMEOUT' "Embeddings compose does not bound Hugging Face download timeout"
assert_contains "extensions/services/embeddings/compose.yaml" 'HF_HUB_ETAG_TIMEOUT' "Embeddings compose does not bound Hugging Face metadata timeout"
assert_contains "installers/macos/install-macos.sh" 'compose-launch\.txt' "macOS installer missing compose launch record"
assert_contains "installers/macos/install-macos.sh" 'ps -q' "macOS installer does not count compose-managed containers"
assert_contains "installers/macos/install-macos.sh" 'docker compose up completed but created no managed containers' "macOS installer does not fail loud on zero managed containers"
assert_contains "installers/macos/install-macos.sh" '_macos_pre_pull_compose_images' "macOS installer does not preflight compose images before launch"
assert_contains "installers/macos/install-macos.sh" '--pull never' "macOS installer still allows implicit compose pulls during install launch"
assert_contains "installers/macos/install-macos.sh" 'ODS_DOCKER_BUILD_MAX_ATTEMPTS' "macOS installer does not retry transient local image build failures"
assert_contains "installers/macos/install-macos.sh" '_macos_build_failed=\$\(\(_macos_build_failed \+ 1\)\)' "macOS installer does not count failed required local image builds"
assert_contains "installers/macos/install-macos.sh" 'refusing to launch stale images' "macOS installer can still launch stale images after required local builds fail"
assert_not_contains "installers/macos/install-macos.sh" 'wait .*\|\| ai_warn "Build failed' "macOS installer still treats required local build failures as warnings"
assert_contains "installers/macos/install-macos.sh" 'colima start --network-address --network-preferred-route' "macOS installer does not prefer the private Colima vmnet route"
assert_contains "installers/macos/install-macos.sh" 'ODS_MACOS_HOST_GATEWAY' "macOS installer does not persist the private Colima host gateway"
assert_contains "installers/macos/install-macos.sh" '_configure_macos_host_agent_bridge' "macOS installer does not bridge host-agent actions over private Colima networking"
assert_contains "installers/macos/install-macos.sh" 'source "\$\{LIB_DIR\}/bridge-manager\.sh"' "macOS installer does not source shared bridge lifecycle code"
assert_contains "installers/macos/ods-macos.sh" 'source "\$\{LIB_DIR\}/bridge-manager\.sh"' "macOS CLI does not source shared bridge lifecycle code"
assert_contains "installers/macos/lib/bridge-manager.sh" 'macos_configure_llm_bridge_from_env' "shared macOS bridge manager does not derive bridge state from .env"
assert_contains "installers/macos/lib/bridge-manager.sh" '--allow-peer' "shared macOS bridge manager does not restrict the Colima peer"
assert_contains "installers/macos/install-macos.sh" '/v1/model/status' "macOS installer does not verify authenticated host-agent reachability from the dashboard container"
assert_contains "installers/macos/install-macos.sh" 'Authorization: Bearer' "macOS installer host-agent readiness probe is not authenticated"
assert_contains "installers/macos/docker-compose.macos.yml" 'ODS_MACOS_HOST_GATEWAY:-host.docker.internal' "macOS compose does not route native inference over the private Colima gateway"
assert_contains "extensions/services/litellm/compose.apple.yaml" 'ODS_MACOS_HOST_GATEWAY:-host-gateway' "macOS LiteLLM overlay does not route native inference over the private Colima gateway"
assert_contains "installers/windows/install-windows.ps1" 'Assert-ODSWindowsManagedContainers' "Windows installer does not assert compose-managed containers"
assert_contains "installers/windows/install-windows.ps1" 'Docker Compose did not create any managed Windows containers' "Windows installer does not fail loud on zero managed containers"
assert_contains "installers/windows/install-windows.ps1" 'dashboard", "dashboard-api", "open-webui' "Windows installer does not require core container services"
assert_contains "installers/windows/install-windows.ps1" 'Invoke-ODSWindowsComposeImagePreflight' "Windows installer does not preflight compose images before launch"
assert_contains "installers/windows/install-windows.ps1" '--pull", "never' "Windows installer still allows implicit compose pulls during install launch"

echo "[contract] Windows host agent ignores Store Python aliases and bootstraps real Python"
assert_contains "installers/windows/phases/07-devtools.ps1" 'Resolve-ODSHostAgentPython' "Windows installer does not resolve a real host-agent Python"
assert_contains "installers/windows/phases/07-devtools.ps1" 'WindowsApps' "Windows installer does not reject Microsoft Store Python aliases"
assert_contains "installers/windows/phases/07-devtools.ps1" 'winget install --exact --id Python\.Python\.3\.12' "Windows installer does not bootstrap Python for host-agent"
assert_contains "installers/windows/phases/07-devtools.ps1" 'PrefixArgs' "Windows installer does not support py launcher -3 arguments"
assert_contains "installers/windows/phases/07-devtools.ps1" 'Start-ScheduledTask -TaskName \$script:ODS_AGENT_TASK_NAME' "Windows installer should start host-agent through Scheduled Tasks"
assert_contains "installers/windows/lib/env-generator.ps1" 'ODS_AGENT_BIND=.*0\.0\.0\.0' "Windows installer should bind host-agent for dashboard-api container access"
assert_contains "installers/windows/ods.ps1" 'Resolve-ODSHostAgentPython' "ods.ps1 agent start does not resolve a real Python"
assert_contains "installers/windows/ods.ps1" 'WindowsApps' "ods.ps1 agent start does not reject Microsoft Store Python aliases"
assert_contains "installers/windows/ods.ps1" 'PrefixArgs' "ods.ps1 agent start does not support py launcher -3 arguments"
assert_contains "installers/windows/ods.ps1" 'Register-ScheduledTask -TaskName \$script:ODS_AGENT_TASK_NAME' "ods.ps1 agent start should use Scheduled Tasks so SSH-launched agents persist"
assert_contains "installers/windows/ods.ps1" 'RedirectStandardError .* -Wait' "ods.ps1 agent task should wait on Python instead of spawning a transient child"
assert_contains "lib/python-cmd.sh" 'windowsapps/python3' "Bash Python resolver should reject WindowsApps python3 aliases"

fake_winapps="$tmpdir/Local/Microsoft/WindowsApps"
fake_realbin="$tmpdir/real-python"
mkdir -p "$fake_winapps" "$fake_realbin"
cat >"$fake_winapps/python3" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
cat >"$fake_realbin/python" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fake_winapps/python3" "$fake_realbin/python"
resolver_out="$(PATH="$fake_winapps:$fake_realbin:$PATH" bash -c '. "$1"; ods_detect_python_cmd' bash "$ROOT_DIR/lib/python-cmd.sh")"
if [[ "$resolver_out" != "python" ]]; then
  echo "Bash Python resolver selected '$resolver_out' instead of real python after a WindowsApps python3 alias" >&2
  exit 1
fi

bash tests/test-macos-cli-mode-routing.sh

echo "[PASS] installer hardening contracts"
