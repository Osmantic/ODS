#!/usr/bin/env bash
# Dashboard API runs as 1000:1000 and persists passwords and chat receipts at
# the shared data root. Preserve the install owner's UID; grant that runtime
# group parent access and retain the API's private chat-result ownership.

ods_prepare_dashboard_data() {
    local install_dir="$1" rootless="$2" target metadata group mode
    target="$install_dir/data"
    if [[ ! -d "$target" || -L "$target" ]]; then
        echo "[error] Dashboard data must be a real directory: $target" >&2
        return 1
    fi
    if [[ "$rootless" == true ]]; then
        # Host chgrp 1000 is not container GID 1000 in a rootless namespace.
        # Reuse the pinned helper; unrelated service data and existing
        # password/chat file modes remain unchanged.
        _ods_rootless_ensure_helper_image || return 1
        docker_run run --rm --network none --user 0:0 \
            --mount "type=bind,src=$target,dst=/data" \
            "$ODS_ROOTLESS_HELPER_IMAGE" sh -ec '
                chgrp 1000 /data
                chmod g+rwx /data
                test "$(stat -c %g /data)" = 1000
                mode=$(stat -c %a /data)
                test "$((0$mode & 070))" = 56
                if test -e /data/pixel-chat-results || test -L /data/pixel-chat-results; then
                    test -d /data/pixel-chat-results && test ! -L /data/pixel-chat-results || exit 1
                    if test "$(stat -c %u:%g /data/pixel-chat-results)" != 1000:1000; then
                        chown -h -R 1000:1000 /data/pixel-chat-results
                    fi
                    test "$(stat -c %u:%g /data/pixel-chat-results)" = 1000:1000
                fi
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
    [[ "$group" == 1000 ]] && (( (8#$mode & 8#070) == 8#070 )) || return 1
    target="$target/pixel-chat-results"
    if [[ -e "$target" || -L "$target" ]]; then
        [[ -d "$target" && ! -L "$target" ]] || return 1
        metadata=$(stat -c '%u:%g' "$target") || return 1
        if [[ "$metadata" != 1000:1000 ]]; then
            # Older generic reinstall repair transferred this private tree
            # to the installing user. Restore it without following symlinks
            # or changing modes, files, or other services' data.
            ods_sudo chown -h -R 1000:1000 "$target" || return 1
        fi
        [[ "$(stat -c '%u:%g' "$target")" == 1000:1000 ]] || return 1
    fi
}
