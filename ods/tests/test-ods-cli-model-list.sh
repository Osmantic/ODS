#!/usr/bin/env bash
# `ods model list` must show what `ods model swap <tier>` would load: the list
# is resolved from the installed tier map, never a hand-maintained copy.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/ods-cli-model-list.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

INSTALL_DIR="$TMP_ROOT/install"
mkdir -p "$INSTALL_DIR/installers/lib" "$INSTALL_DIR/data/models"
cp "$ROOT_DIR/installers/lib/tier-map.sh" "$INSTALL_DIR/installers/lib/tier-map.sh"
touch "$INSTALL_DIR/docker-compose.base.yml" "$INSTALL_DIR/.env"
printf 'model\n' > "$INSTALL_DIR/data/models/Qwen3.5-9B-Q4_K_M.gguf"

output="$(ODS_HOME="$INSTALL_DIR" NO_COLOR=1 HOST_ARCH=amd64 "$ROOT_DIR/ods-cli" model list 2>&1)"

expected_model() {
    (
        error() { printf '%s\n' "$*" >&2; exit 1; }
        # shellcheck source=/dev/null
        . "$ROOT_DIR/installers/lib/tier-map.sh"
        TIER="$1"
        resolve_tier_config >/dev/null
        printf '%s %s\n' "$LLM_MODEL" "$GGUF_FILE"
    )
}

failures=0
for pair in T0:0 T1:1 T2:2 T3:3 T4:4 SH:SH_COMPACT SH_LARGE:SH_LARGE NV_ULTRA:NV_ULTRA ARC:ARC ARC_LITE:ARC_LITE; do
    label="${pair%%:*}" tier="${pair#*:}"
    read -r model gguf < <(HOST_ARCH=amd64 expected_model "$tier")
    line="$(grep -E "^  ${label} " <<< "$output" || true)"
    if [[ -z "$line" || "$line" != *" $model "* ]]; then
        echo "[FAIL] $label should list $model; got: ${line:-<missing>}"
        failures=$((failures + 1))
    fi
    downloaded=false
    [[ "$gguf" == "Qwen3.5-9B-Q4_K_M.gguf" ]] && downloaded=true
    if [[ "$downloaded" == true && "$line" != *"[downloaded]"* ]] \
        || [[ "$downloaded" == false && "$line" == *"[downloaded]"* ]]; then
        echo "[FAIL] $label download marker is wrong: $line"
        failures=$((failures + 1))
    fi
done
if grep -q 'qwen3-30b-a3b' <<< "$output"; then
    echo "[FAIL] model list still names a retired tier model"
    failures=$((failures + 1))
fi

[[ "$failures" -eq 0 ]] || { printf '%s\n' "$output"; exit 1; }

# A gemma4 install (or auto, which resolves to Gemma above tier 0) lists the
# Gemma family that `swap` would load, from the install's own .env profile
# rather than the caller's shell.
printf 'model\n' > "$INSTALL_DIR/data/models/gemma-4-E2B-it-Q4_K_M.gguf"
for profile in '"gemma4"' auto; do
    printf 'MODEL_PROFILE=%s\n' "$profile" > "$INSTALL_DIR/.env"
    profile_output="$(env -u MODEL_PROFILE ODS_HOME="$INSTALL_DIR" NO_COLOR=1 HOST_ARCH=amd64 \
        "$ROOT_DIR/ods-cli" model list 2>&1)"
    read -r model gguf < <(MODEL_PROFILE=gemma4 HOST_ARCH=amd64 expected_model 1)
    line="$(grep -E '^  T1 ' <<< "$profile_output" || true)"
    if [[ "$line" != *" $model "* || "$line" != *"[downloaded]"* ]]; then
        echo "[FAIL] MODEL_PROFILE=$profile T1 should list downloaded $model ($gguf); got: ${line:-<missing>}"
        failures=$((failures + 1))
    fi
done

[[ "$failures" -eq 0 ]] || { printf '%s\n' "$profile_output"; exit 1; }
echo "[PASS] ods model list matches the tier map and marks downloaded models"
