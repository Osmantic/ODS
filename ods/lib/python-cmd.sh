#!/usr/bin/env bash

# ODS: Python command resolver
# Goal: Prefer python3 when available, but gracefully fall back to python (common on some Windows setups).
# This file is sourced by other scripts, so it must not change the caller's shell options.

_ods_python_cmd_cached=""

_ods_python_resolved_path() {
    local candidate="$1"
    command -v "$candidate" 2>/dev/null || printf '%s' "$candidate"
}

_ods_python_is_windowsapps_alias() {
    local resolved_lc
    resolved_lc="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr '\\' '/')"
    case "$resolved_lc" in
        */windowsapps/python|*/windowsapps/python.exe|*/windowsapps/python3|*/windowsapps/python3.exe|*/windowsapps/*/python.exe)
            return 0
            ;;
    esac
    return 1
}

_ods_python_runnable() {
    local candidate="$1"
    local resolved
    [[ -n "$candidate" ]] || return 1
    command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]] || return 1
    resolved="$(_ods_python_resolved_path "$candidate")"
    if _ods_python_is_windowsapps_alias "$resolved"; then
        return 1
    fi
    "$candidate" -c 'import sys; sys.exit(0)' >/dev/null 2>&1
}

_ods_python_has_module() {
    local candidate="$1" module="$2"
    _ods_python_runnable "$candidate" || return 1
    "$candidate" - "$module" <<'PY' >/dev/null 2>&1
import importlib
import sys

importlib.import_module(sys.argv[1])
PY
}

_ods_windows_path_to_unix() {
    local path="$1" drive rest
    path="${path//\\//}"
    if [[ "$path" =~ ^([A-Za-z]):/(.*)$ ]]; then
        drive="$(printf '%s' "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
        rest="${BASH_REMATCH[2]}"
        printf '/%s/%s' "$drive" "$rest"
    else
        printf '%s' "$path"
    fi
}

_ods_python_windows_candidates() {
    local local_appdata=""
    if [[ -n "${LOCALAPPDATA:-}" ]]; then
        local_appdata="$(_ods_windows_path_to_unix "$LOCALAPPDATA")"
    elif [[ -n "${USERPROFILE:-}" ]]; then
        local_appdata="$(_ods_windows_path_to_unix "$USERPROFILE")/AppData/Local"
    fi
    [[ -n "$local_appdata" ]] || return 0

    local candidate
    for candidate in \
        "$local_appdata"/Programs/Python/Python*/python.exe \
        "$local_appdata"/Python/bin/python.exe
    do
        [[ -e "$candidate" ]] && printf '%s\n' "$candidate"
    done
}

# Prints the python command name to stdout.
# Order:
#  1) python3 (must be runnable)
#  2) python  (must be runnable)
# Exits non-zero if neither works.
ods_detect_python_cmd() {
    if [[ -n "${_ods_python_cmd_cached}" ]]; then
        printf '%s' "${_ods_python_cmd_cached}"
        return 0
    fi

    if [[ -n "${ODS_PYTHON_CMD:-}" ]] && _ods_python_runnable "$ODS_PYTHON_CMD"; then
        _ods_python_cmd_cached="$ODS_PYTHON_CMD"
        printf '%s' "${_ods_python_cmd_cached}"
        return 0
    fi

    # Linux installer paths install Python modules through the system package
    # manager. Prefer /usr/bin/python3 when requested so a Conda/venv python3
    # ahead of PATH does not miss apt/dnf-installed modules like PyYAML.
    if [[ "${ODS_PYTHON_PREFER_SYSTEM:-}" == "1" && -x /usr/bin/python3 ]]; then
        if _ods_python_runnable /usr/bin/python3; then
            _ods_python_cmd_cached="/usr/bin/python3"
            printf '%s' "${_ods_python_cmd_cached}"
            return 0
        fi
    fi

    if _ods_python_runnable python3; then
        _ods_python_cmd_cached="python3"
        printf '%s' "${_ods_python_cmd_cached}"
        return 0
    fi

    if _ods_python_runnable python; then
        _ods_python_cmd_cached="python"
        printf '%s' "${_ods_python_cmd_cached}"
        return 0
    fi

    echo "ERROR: Neither python3 nor python is available/runnable." >&2
    return 1
}

# Prints a Python command that can import the requested module.
# This is intentionally separate from ods_detect_python_cmd: generic scripts
# should still prefer python3, while YAML/JSON-schema helpers need the
# interpreter that actually has their dependency installed. On Windows Git
# Bash, python3 can resolve to a Microsoft Store/App Installer Python without
# PyYAML while python has the module, so "runnable" is not enough.
ods_detect_python_cmd_with_module() {
    local module="$1"
    [[ -n "$module" ]] || return 1

    if [[ -n "${ODS_PYTHON_CMD:-}" ]] && _ods_python_has_module "$ODS_PYTHON_CMD" "$module"; then
        printf '%s' "$ODS_PYTHON_CMD"
        return 0
    fi

    if [[ "${ODS_PYTHON_PREFER_SYSTEM:-}" == "1" && -x /usr/bin/python3 ]]; then
        if _ods_python_has_module /usr/bin/python3 "$module"; then
            printf '%s' "/usr/bin/python3"
            return 0
        fi
    fi

    local candidate
    for candidate in python3 python; do
        if _ods_python_has_module "$candidate" "$module"; then
            printf '%s' "$candidate"
            return 0
        fi
    done

    while IFS= read -r candidate; do
        if _ods_python_has_module "$candidate" "$module"; then
            printf '%s' "$candidate"
            return 0
        fi
    done < <(_ods_python_windows_candidates)

    return 1
}
