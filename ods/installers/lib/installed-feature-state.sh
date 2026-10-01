#!/usr/bin/env bash
# Recover an installed optional service's last Compose selection before a
# rerun copies fresh source files into INSTALL_DIR. An active compose.yaml wins
# if an old upgrade left both names behind, matching the stack resolver.
ods_installed_service_default() {
    local install_dir="$1" service="$2" fallback="$3"
    local compose="$install_dir/extensions/services/$service/compose.yaml"
    # Only an installed tree (its .env exists) has a previous selection to
    # honor. A fresh source layout may carry optional compose files for every
    # service, so an existing compose.yaml there must NOT enable a feature.
    if [[ ! -f "$install_dir/.env" ]]; then
        printf '%s\n' "$fallback"
    elif [[ -f "$compose" ]]; then
        printf '%s\n' true
    elif [[ -f "${compose}.disabled" ]]; then
        printf '%s\n' false
    else
        printf '%s\n' "$fallback"
    fi
}

# Portal can replace WebUI as chat only for an ordinary fresh install with a
# qualified Pixel runtime and no selected WebUI-dependent feature. Voice uses
# ODS Talk through Portal and does not require Open WebUI. Keep this
# decision after phase 03's host check, but before image pulls and persistence.
ods_should_default_portal_chat() {
    local existing="$1" explicit="$2" gateway="$3" pixel_runtime="$4"
    # Keep argument 5 in the existing call contract; selected voice services
    # use ODS Talk through Portal and no longer block Portal as the chat UI.
    local rag="$6" proxy="$7"
    [[ "$existing" != true && "$explicit" != true && "$gateway" != true &&
       "$pixel_runtime" == true && "$rag" != true &&
       "$proxy" != true ]]
}
