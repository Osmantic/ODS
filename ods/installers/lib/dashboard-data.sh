#!/usr/bin/env bash
# Dashboard API runs as 1000:1000 and persists passwords and chat receipts at
# the shared data root. Preserve the install owner's UID and every child;
# grant only that runtime group access to the parent directory.

ods_prepare_dashboard_data() {
    local install_dir="$1" rootless="$2" target metadata group mode
    target="$install_dir/data"
    if [[ ! -d "$target" || -L "$target" ]]; then
        echo "[error] Dashboard data must be a real directory: $target" >&2
        return 1
    fi
    if [[ "$rootless" == true ]]; then
        # Host chgrp 1000 is not container GID 1000 in a rootless namespace.
        # Reuse the pinned helper; changing only the parent does not disturb
        # private Hermes/service data or existing password/chat file modes.
        _ods_rootless_ensure_helper_image || return 1
        docker_run run --rm --network none --user 0:0 \
            --mount "type=bind,src=$target,dst=/data" \
            "$ODS_ROOTLESS_HELPER_IMAGE" sh -ec '
                chgrp 1000 /data
                chmod g+rwx /data
                test "$(stat -c %g /data)" = 1000
                mode=$(stat -c %a /data)
                test "$((0$mode & 070))" = 56
            '
        return $?
    fi
    metadata=$(stat -c '%g:%a' "$target") || return 1
    IFS=: read -r group mode <<< "$metadata"
    if [[ "$group" != 1000 ]]; then
        ods_sudo chgrp 1000 "$target" || return 1
    fi
    if (( (8#$mode & 8#070) != 8#070 )); then
        ods_sudo chmod g+rwx "$target" || return 1
    fi
    metadata=$(stat -c '%g:%a' "$target") || return 1
    IFS=: read -r group mode <<< "$metadata"
    [[ "$group" == 1000 ]] && (( (8#$mode & 8#070) == 8#070 ))
}
