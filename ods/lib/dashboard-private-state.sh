#!/bin/bash
# The logical backup path stays data/pixel-images on every platform. A Mac
# installation selecting private volumes must never back up its stale host copy.
ods_private_images_selected() {
    [[ -e "$ODS_DIR/data/.dashboard-private-stores/pixel-images.json" \
        || -L "$ODS_DIR/data/.dashboard-private-stores/pixel-images.json" ]] \
        || { [[ -f "$ODS_DIR/.compose-flags" ]] \
            && grep -Fq 'installers/macos/dashboard-private-state.compose.yaml' "$ODS_DIR/.compose-flags"; }
}

ods_private_images() {
    python3 "$ODS_DIR/installers/macos/lib/dashboard-private-state.py" \
        "$@" --install-dir "$ODS_DIR"
}
