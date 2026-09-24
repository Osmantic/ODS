#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT_DIR/scripts/upgrade-model.sh"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

install_root="$TMP_DIR/install"
models_root="$install_root/data/models"
mkdir -p "$models_root/current-model" "$models_root/next-model"
printf '{}\n' > "$models_root/current-model/config.json"
printf '{}\n' > "$models_root/next-model/config.json"
printf 'LLM_MODEL=current-model\n' > "$install_root/.env"
printf '{"current":"current-model","previous":""}\n' > "$install_root/model-state.json"

before="$(find "$install_root" -type f -exec cksum {} + | sort)"
output="$(ODS_DIR="$install_root" MODELS_DIR="$models_root" bash "$SCRIPT" --dry-run next-model)"
after="$(find "$install_root" -type f -exec cksum {} + | sort)"

[[ "$before" == "$after" ]] || fail "preview changed installation files"
grep -qF "Current model: current-model" <<< "$output" || fail "preview omitted current model"
grep -qF "Target model:  next-model" <<< "$output" || fail "preview omitted target model"
grep -qF "1. Stop llama-server" <<< "$output" || fail "preview omitted stop phase"
grep -qF "5. Verify health and inference; roll back on failure" <<< "$output" \
    || fail "preview omitted verification and rollback phase"

alternate="$(ODS_DIR="$install_root" MODELS_DIR="$models_root" bash "$SCRIPT" next-model --dry-run)"
[[ "$alternate" == "$output" ]] || fail "trailing --dry-run form changed the plan"

if ODS_DIR="$install_root" MODELS_DIR="$models_root" \
    bash "$SCRIPT" --dry-run missing-model >"$TMP_DIR/missing.log" 2>&1; then
    fail "preview accepted a missing model"
fi
grep -qF "Model not found: missing-model" "$TMP_DIR/missing.log" \
    || fail "missing-model failure was not actionable"

same="$(ODS_DIR="$install_root" MODELS_DIR="$models_root" bash "$SCRIPT" --dry-run current-model)"
grep -qF "none (target is already active)" <<< "$same" \
    || fail "preview did not identify a no-op upgrade"

echo "[PASS] model upgrade preview is complete and read-only"
