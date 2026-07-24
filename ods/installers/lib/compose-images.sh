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

_ods_compose_python_cmd() {
    if [[ -n "${ODS_PYTHON_CMD:-}" && -x "${ODS_PYTHON_CMD:-}" ]]; then
        printf '%s\n' "$ODS_PYTHON_CMD"
        return 0
    fi
    command -v python3 2>/dev/null || command -v python 2>/dev/null || return 1
}

_ods_compose_is_local_image() {
    local entry="${1:-}"
    local image="${entry%%|*}"
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
    platform = str(service.get("platform") or "").strip()
    if image:
        if platform:
            print(f"{image}|{platform}")
        else:
            print(image)
' | _ods_compose_filter_external_images; then
            return 0
        fi
    fi

    # Older Compose builds may lack JSON output. This fallback can include
    # generated build tags, so the local-image filter below remains important.
    $compose_cmd "${compose_flags[@]}" config --images 2>/dev/null | _ods_compose_filter_external_images
}
