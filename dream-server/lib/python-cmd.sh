#!/usr/bin/env bash

# Dream Server: Python command resolver
# Goal: Prefer python3 when available, but gracefully fall back to python (common on some Windows setups).
# This file is sourced by other scripts, so it must not change the caller's shell options.

_ds_python_cmd_cached=""

_ds_python_runnable() {
    local candidate="$1"
    [[ -n "$candidate" ]] || return 1
    command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]] || return 1
    "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1
}

# Prints the python command name to stdout.
# Order:
#  1) python3 (must be runnable)
#  2) python  (must be runnable)
# Exits non-zero if neither works.
ds_detect_python_cmd() {
    if [[ -n "${_ds_python_cmd_cached}" ]]; then
        printf '%s' "${_ds_python_cmd_cached}"
        return 0
    fi

    if [[ -n "${DREAM_PYTHON_CMD:-}" ]] && _ds_python_runnable "$DREAM_PYTHON_CMD"; then
        _ds_python_cmd_cached="$DREAM_PYTHON_CMD"
        printf '%s' "${_ds_python_cmd_cached}"
        return 0
    fi

    # Linux installer paths install Python modules through the system package
    # manager. Prefer /usr/bin/python3 when requested so a Conda/venv python3
    # ahead of PATH does not miss apt/dnf-installed modules like PyYAML.
    if [[ "${DREAM_PYTHON_PREFER_SYSTEM:-}" == "1" && -x /usr/bin/python3 ]]; then
        if _ds_python_runnable /usr/bin/python3; then
            _ds_python_cmd_cached="/usr/bin/python3"
            printf '%s' "${_ds_python_cmd_cached}"
            return 0
        fi
    fi

    if _ds_python_runnable python3; then
        _ds_python_cmd_cached="python3"
        printf '%s' "${_ds_python_cmd_cached}"
        return 0
    fi

    if _ds_python_runnable python; then
        _ds_python_cmd_cached="python"
        printf '%s' "${_ds_python_cmd_cached}"
        return 0
    fi

    echo "ERROR: Neither python3 nor python is available/runnable." >&2
    return 1
}
