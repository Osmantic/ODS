#!/bin/bash
# ============================================================================
# ODS Installer — Lemonade-era settings migration
# ============================================================================
# Part of: installers/lib/
# Purpose: Run scripts/migrate-lemonade-install.py, which moves a Lemonade-era
#          .env to the llama.cpp runtime, and report what it changed. ODS no
#          longer installs or launches Lemonade; existing installs keep their
#          model files and active model.
#
# Expects: SCRIPT_DIR, LOG_FILE, log(), ai(), ai_warn()
# Provides: ods_migrate_lemonade_env()
#
# Modder notes:
#   Runs before Phase 02, which preserves the active model only for
#   ODS_MODE=local, and before validate-env.sh. Unchanged installs print
#   "lemonade-migration: none". Remove one release after round F.
# ============================================================================

# Usage: ods_migrate_lemonade_env INSTALL_DIR
ods_migrate_lemonade_env() {
    local install_dir="$1" python_cmd="${ODS_PYTHON_CMD:-}" output status=0 line
    if [[ -z "$python_cmd" && -f "$SCRIPT_DIR/lib/python-cmd.sh" ]]; then
        # shellcheck source=../../lib/python-cmd.sh
        . "$SCRIPT_DIR/lib/python-cmd.sh"
        python_cmd="$(ods_detect_python_cmd)" || python_cmd=""
    fi
    python_cmd="${python_cmd:-python3}"
    if ! command -v "$python_cmd" >/dev/null 2>&1; then
        log "Python 3 is required to check this installation for Lemonade-era settings."
        return 1
    fi
    output="$("$python_cmd" "$SCRIPT_DIR/scripts/migrate-lemonade-install.py" env \
        --install-dir "$install_dir" 2>&1)" || status=$?
    printf '%s\n' "$output" >> "$LOG_FILE"
    [[ "$status" -eq 0 ]] || return 1
    [[ "$output" != "lemonade-migration: none"* ]] || return 0
    while IFS= read -r line; do
        case "$line" in
            "lemonade-migration: managed") ai "Moved the AMD settings from Lemonade to the llama.cpp runtime; your model files and active model are kept." ;;
            "lemonade-migration: portal") ai "Moved the Windows GPU route from Lemonade to the native llama-server settings." ;;
            "lemonade-migration: external") ai_warn "ODS no longer manages Lemonade. Your Lemonade server is now used as a generic OpenAI-compatible endpoint (EXTERNAL_LLM_URL)." ;;
            "lemonade-migration: changed "*) log "${line#lemonade-migration: }" ;;
            "lemonade-migration: "*) ai "${line#lemonade-migration: }" ;;
        esac
    done <<< "$output"
}
