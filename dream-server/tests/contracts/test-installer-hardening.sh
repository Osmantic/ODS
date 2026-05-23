#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

assert_contains() {
  local file="$1"
  local pattern="$2"
  local msg="$3"
  if ! grep -qE "$pattern" "$file"; then
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
  if grep -qE "$pattern" "$file"; then
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

missing_yaml_err="$tmpdir/missing-yaml.err"
if DREAM_PYTHON_CMD="$fake_py" scripts/resolve-compose-stack.sh --script-dir "$ROOT_DIR" >"$tmpdir/missing-yaml.out" 2>"$missing_yaml_err"; then
  echo "[FAIL] resolver should fail when selected Python cannot import yaml"
  exit 1
fi
assert_contains "$missing_yaml_err" 'PyYAML is required' "resolver did not explain PyYAML requirement"
assert_contains "$missing_yaml_err" 'conda deactivate' "resolver did not include Conda/venv recovery hint"

echo "[contract] macOS PyYAML install falls back to private installer venv"
macos_installer="installers/macos/install-macos.sh"
assert_contains "$macos_installer" '_ensure_macos_pyyaml' "macOS installer missing PyYAML helper"
assert_contains "$macos_installer" 'python-cmd.sh' "macOS installer does not load python command resolver"
assert_contains "$macos_installer" 'python3 -m pip --user' "macOS installer no longer tries user-site PyYAML first"
assert_contains "$macos_installer" '\.installer-venv' "macOS installer missing private venv fallback"
assert_contains "$macos_installer" 'export DREAM_PYTHON_CMD' "macOS installer does not export selected Python"
assert_contains "$macos_installer" '_ds_python_cmd_cached=' "macOS installer does not refresh python resolver cache"

echo "[contract] Linux phase 06 reports substeps on failure"
phase06="installers/phases/06-directories.sh"
assert_contains "$phase06" 'export INSTALL_PHASE="06-directories/\$\{step\}"' "phase 06 missing substep INSTALL_PHASE updates"
for step in create-directories copy-source copy-extensions-library generate-env validate-env generate-searxng-config; do
  assert_contains "$phase06" "_phase06_step \"$step\"" "phase 06 missing substep: $step"
done

echo "[contract] ds_sudo uses sudo -n in non-interactive mode"
fakebin="$tmpdir/fakebin"
mkdir -p "$fakebin"
cat > "$fakebin/sudo" <<'SUDOEOT'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$DREAM_TEST_SUDO_ARGS"
exit 0
SUDOEOT
chmod +x "$fakebin/sudo"
sudo_args="$tmpdir/sudo.args"
PATH="$fakebin:$PATH" DREAM_TEST_SUDO_ARGS="$sudo_args" bash -c '
  set -euo pipefail
  INTERACTIVE=false
  DRY_RUN=false
  source installers/lib/sudo.sh
  ds_sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
'
assert_contains "$sudo_args" '^-n nvidia-ctk cdi generate' "ds_sudo did not use sudo -n for non-interactive runs"

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
assert_contains "installers/macos/install-macos.sh" 'compose-launch\.txt' "macOS installer missing compose launch record"
assert_contains "installers/macos/install-macos.sh" 'ps -q' "macOS installer does not count compose-managed containers"
assert_contains "installers/macos/install-macos.sh" 'docker compose up completed but created no managed containers' "macOS installer does not fail loud on zero managed containers"

echo "[PASS] installer hardening contracts"
