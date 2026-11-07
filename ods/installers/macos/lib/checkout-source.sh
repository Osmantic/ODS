#!/bin/bash
# Pass the actual checkout to native source-provenance checks. The installed
# copy deliberately has no Git metadata; bootstrap may already supply a pin.
ods_macos_select_checkout_source() {
    local source_root="$1"
    if [[ -z "${ODS_BOOTSTRAP_SOURCE_DIR+x}" ]] && command -v git >/dev/null 2>&1 \
            && [[ "$(git -C "$source_root" rev-parse --is-inside-work-tree 2>/dev/null)" == true ]]; then
        export ODS_BOOTSTRAP_SOURCE_DIR="$source_root"
    fi
}
