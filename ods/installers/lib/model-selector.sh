#!/usr/bin/env bash
# ============================================================================
# ODS Installer -- catalog model selector calls
# ============================================================================
# Part of: installers/lib/
# Purpose: One place for the Linux installer's calls into
#          scripts/select-model.py. Phase 02 picks the model; phase 03
#          re-checks it at the Hermes context floor with the same hardware
#          envelope, so the two can never plan against different inputs.
#
# Expects: SCRIPT_DIR, GPU_BACKEND, GPU_MEMORY_TYPE, GPU_VRAM, RAM_GB, TIER,
#          HOST_ARCH, MODEL_PROFILE_EFFECTIVE (or MODEL_PROFILE)
# Provides: ODS_HERMES_MIN_CONTEXT, ods_model_selector_python,
#           ods_run_catalog_selector, ods_catalog_fit_check
#
# This file is sourced; it must not change the caller's shell options.
# ============================================================================

# Hermes Agent refuses a model below 64K; select-model.py prefers models that
# fit at this context (a soft floor) unless --require-min-context is passed.
ODS_HERMES_MIN_CONTEXT=65536

# Print the Python command for the selector, or nothing when none is usable.
ods_model_selector_python() {
    local python=""
    if [[ -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
        # shellcheck source=/dev/null
        . "$SCRIPT_DIR/lib/python-cmd.sh"
        python="$(ods_detect_python_cmd 2>/dev/null || true)"
    fi
    if [[ -z "$python" ]]; then
        if command -v python3 >/dev/null 2>&1; then
            python="python3"
        elif command -v python >/dev/null 2>&1; then
            python="python"
        fi
    fi
    printf '%s' "$python"
}

# ods_run_catalog_selector PYTHON MAX_SIZE_MB [select-model.py args...]
# Prints shell assignments (--env). Exit 2: nothing fits.
ods_run_catalog_selector() {
    local python="$1" max_size_mb="${2:-0}"
    shift 2
    "$python" "$SCRIPT_DIR/scripts/select-model.py" \
        --catalog "$SCRIPT_DIR/config/model-library.json" \
        --backend "${GPU_BACKEND:-unknown}" \
        --memory-type "${GPU_MEMORY_TYPE:-discrete}" \
        --vram-mb "${GPU_VRAM:-0}" \
        --ram-gb "${RAM_GB:-0}" \
        --profile "${MODEL_PROFILE_EFFECTIVE:-${MODEL_PROFILE:-qwen}}" \
        --tier "${TIER:-1}" \
        --max-size-mb "$max_size_mb" \
        --host-arch "${HOST_ARCH:-unknown}" \
        --installable-only \
        "$@" \
        --env
}

# ods_catalog_fit_check PYTHON MODEL CONTEXT [RUNTIME_PROFILE]
# MODEL is a catalog id, LLM_MODEL name or GGUF file name. Exit 0: fits at
# CONTEXT on this hardware; 3: does not fit; anything else: unknown (the
# model is not in the catalog, or the selector failed).
ods_catalog_fit_check() {
    local python="$1" model="$2" context="$3" profile="${4:-}"
    local -a profile_args=()
    [[ -n "$profile" ]] && profile_args=(--runtime-profile "$profile")
    "$python" "$SCRIPT_DIR/scripts/select-model.py" \
        --catalog "$SCRIPT_DIR/config/model-library.json" \
        --backend "${GPU_BACKEND:-unknown}" \
        --memory-type "${GPU_MEMORY_TYPE:-discrete}" \
        --vram-mb "${GPU_VRAM:-0}" \
        --ram-gb "${RAM_GB:-0}" \
        --profile "${MODEL_PROFILE_EFFECTIVE:-${MODEL_PROFILE:-qwen}}" \
        --tier "${TIER:-1}" \
        --host-arch "${HOST_ARCH:-unknown}" \
        --check-fit --model-id "$model" --context "$context" \
        ${profile_args[@]+"${profile_args[@]}"}
}
