#!/bin/bash
# ============================================================================
# Dream Server Installer — Python runtime guards
# ============================================================================
# Part of: installers/lib/
# Purpose: Ensure installer Python helpers use an interpreter that can import
#          the modules they need. This especially protects Linux users with
#          Conda/venv Python ahead of /usr/bin/python3 in PATH.
#
# Expects: SCRIPT_DIR, LOG_FILE, PKG_MANAGER, INTERACTIVE, DRY_RUN,
#          pkg_install(), pkg_resolve(), ds_sudo(), ai(), ai_ok(), ai_warn(),
#          error()
# Provides: ds_python_cmd_path(), ds_python_has_module(),
#           ds_ensure_python_module()
# ============================================================================

[[ -f "${SCRIPT_DIR:-$(pwd)}/lib/python-cmd.sh" ]] && . "${SCRIPT_DIR:-$(pwd)}/lib/python-cmd.sh"

ds_python_cmd_path() {
    local cmd="${1:-$(ds_detect_python_cmd 2>/dev/null || true)}"
    [[ -z "$cmd" ]] && return 1
    command -v "$cmd" 2>/dev/null || printf '%s\n' "$cmd"
}

ds_python_has_module() {
    local module="$1"
    local pycmd="${2:-$(ds_detect_python_cmd)}"
    "$pycmd" -c "import ${module}" >/dev/null 2>&1
}

ds_python_is_env_managed() {
    local py_path="${1:-$(ds_python_cmd_path 2>/dev/null || true)}"

    [[ -n "${CONDA_PREFIX:-}" || -n "${VIRTUAL_ENV:-}" ]] && return 0
    [[ "$py_path" == *"/conda/"* || "$py_path" == *"/miniconda"* || "$py_path" == *"/anaconda"* ]] && return 0
    [[ "$py_path" == *"/.venv/"* || "$py_path" == *"/venv/"* ]] && return 0
    return 1
}

ds_ensure_python_module() {
    local module="$1"
    local canonical_pkg="$2"
    local pip_pkg="$3"
    local display="${4:-$module}"

    local pycmd
    pycmd="$(ds_detect_python_cmd)" || error "Python is required but no runnable python3/python was found."

    if ds_python_has_module "$module" "$pycmd"; then
        ai_ok "$display available for $pycmd"
        return 0
    fi

    if [[ "${DRY_RUN:-false}" == "true" ]]; then
        ai_warn "$display is not importable by $pycmd (dry-run: would install ${canonical_pkg})."
        return 0
    fi

    if declare -f pkg_install >/dev/null 2>&1 && declare -f pkg_resolve >/dev/null 2>&1; then
        ai "Installing $display for the installer Python runtime..."
        # shellcheck disable=SC2046
        pkg_install $(pkg_resolve "$canonical_pkg") 2>>"${LOG_FILE:-/dev/null}" || true
    fi

    if ds_python_has_module "$module" "$pycmd"; then
        ai_ok "$display available for $pycmd"
        return 0
    fi

    local py_path
    py_path="$(ds_python_cmd_path "$pycmd" 2>/dev/null || printf '%s' "$pycmd")"
    if ds_python_is_env_managed "$py_path"; then
        ai_bad "$display is not importable by the active Python: $py_path"
        ai_bad "A Conda/venv Python appears to be ahead of the system Python."
        ai "Run: conda deactivate"
        ai "Then re-run the installer, or install the module into that environment:"
        ai "  $pycmd -m pip install $pip_pkg"
    else
        ai_bad "$display is not importable by $pycmd."
        ai "Install it manually and re-run:"
        ai "  $pycmd -m pip install $pip_pkg"
    fi
    error "$display is required by the Dream Server compose and service registry helpers."
}
