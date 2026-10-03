#!/bin/bash
# ============================================================================
# ODS Installer -- Docker Compose image discovery
# ============================================================================
# Part of: installers/lib/
# Purpose: Resolve remote service images from the final Docker Compose stack.
#
# Provides:
#   ods_compose_external_images <compose-cmd> [compose flags...]
# ============================================================================

# Resolve the interpreter to run the JSON filter below.
#
# ODS_PYTHON_CMD is a *command*, not necessarily a path: install-macos.sh's
# _set_installer_python_cmd and scripts/pre-download.sh both export the plain
# name "python3" as often as they export a venv path. Testing only `-x` made
# the name form fail the check, so the interpreter the installer deliberately
# selected was silently ignored in favour of whatever `command -v python3`
# finds first — which lib/python-cmd.sh exists to avoid, because on Windows
# that can be a non-functional Microsoft Store alias.
_ods_compose_python_cmd() {
    if [[ -n "${ODS_PYTHON_CMD:-}" ]]; then
        if [[ -x "$ODS_PYTHON_CMD" ]] || command -v "$ODS_PYTHON_CMD" >/dev/null 2>&1; then
            printf '%s\n' "$ODS_PYTHON_CMD"
            return 0
        fi
    fi
    command -v python3 2>/dev/null || command -v python 2>/dev/null || return 1
}

_ods_compose_is_local_image() {
    local image="${1:-}"
    case "$image" in
        ""|ods-*|ods-*:*|docker.io/library/ods-*|localhost/*|localhost:*/*|127.0.0.1:*/*)
            return 0
            ;;
    esac
    return 1
}

_ods_compose_filter_external_images() {
    local image
    while IFS= read -r image; do
        image="${image%%[[:space:]]*}"
        [[ -n "$image" ]] || continue
        _ods_compose_is_local_image "$image" && continue
        printf '%s\n' "$image"
    done | awk '!seen[$0]++'
}

ods_compose_external_images() {
    local compose_cmd="${1:-docker compose}"
    shift || true
    local -a compose_flags=("$@")
    local py config_json

    py="$(_ods_compose_python_cmd 2>/dev/null || true)"
    if [[ -n "$py" ]] && config_json="$($compose_cmd "${compose_flags[@]}" config --format json 2>/dev/null)"; then
        if printf '%s' "$config_json" | "$py" -c '
import json
import sys

try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)

for service in (data.get("services") or {}).values():
    if service.get("build") is not None:
        continue
    image = str(service.get("image") or "").strip()
    if image:
        print(image)
' | _ods_compose_filter_external_images; then
            return 0
        fi
    fi

    # Older Compose builds may lack JSON output. This fallback can include
    # generated build tags, so the local-image filter below remains important.
    $compose_cmd "${compose_flags[@]}" config --images 2>/dev/null | _ods_compose_filter_external_images
}
