#!/usr/bin/env bash
# Recover an installed optional service's last Compose selection before a
# rerun copies fresh source files into INSTALL_DIR. An active compose.yaml wins
# if an old upgrade left both names behind, matching the stack resolver.
ods_installed_service_default() {
    local install_dir="$1" service="$2" fallback="$3"
    local compose="$install_dir/extensions/services/$service/compose.yaml"
    if [[ -f "$compose" ]]; then
        printf '%s\n' true
    elif [[ -f "${compose}.disabled" ]]; then
        printf '%s\n' false
    else
        printf '%s\n' "$fallback"
    fi
}
