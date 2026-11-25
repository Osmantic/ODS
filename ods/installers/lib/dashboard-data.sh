#!/usr/bin/env bash
# Dashboard API runs as 1000:1000 and persists passwords and chat receipts at
# the shared data root. Preserve the install owner's UID; grant that runtime
# group parent access and retain the API's private chat and image stores.

# Use available privilege, or let ordinary filesystem permissions decide.
# ods_sudo deliberately skips optional work when privilege is unavailable.
_ods_dashboard_mutate() {
    if declare -f ods_sudo_available >/dev/null 2>&1 && ods_sudo_available; then
        ods_sudo "$@"
    else
        "$@"
    fi
}

_ods_dashboard_lock_helper_image() {
    # Reuse the Dashboard API's pinned Python base. BusyBox has no Python.
    ODS_DASHBOARD_LOCK_IMAGE='python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e'
    docker_run image inspect "$ODS_DASHBOARD_LOCK_IMAGE" >/dev/null 2>&1 ||
        docker_run pull "$ODS_DASHBOARD_LOCK_IMAGE"
}

ods_prepare_dashboard_data() {
    local install_dir="$1" rootless="$2" target private_store models metadata group mode lock lock_helper use_docker_repair=false
    lock_helper="${SCRIPT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/installers/lib/dashboard-lock.py"
    target="$install_dir/data"
    if [[ ! -d "$target" || -L "$target" ]]; then
        echo "[error] Dashboard data must be a real directory: $target" >&2
        return 1
    fi
    # The API inventories weights through this bind mount. Grant only its
    # runtime group directory access; model file permissions stay untouched.
    models="$target/models"
    if [[ -e "$models" || -L "$models" ]]; then
        [[ -d "$models" && ! -L "$models" ]] || {
            echo "[error] Model data must be a real directory: $models" >&2
            return 1
        }
    fi
    # Create as the installer, without sudo. An existing unreadable lock can be
    # inspected with O_PATH; neither creation nor repair follows a replacement
    # symlink. Privileged repair below keeps the same flock inode.
    lock="$target/.extensions-lock"
    python3 "$lock_helper" prepare "$target" >/dev/null || return 1
    if [[ "$rootless" == true ]]; then
        use_docker_repair=true
    elif declare -f ods_sudo_available >/dev/null 2>&1 && ! ods_sudo_available; then
        # A non-root installer outside group 1000 cannot chgrp its own data
        # directory to the Dashboard runtime's GID. Docker group access is
        # sufficient for this narrowly scoped bind-mount repair.
        metadata=$(stat -c '%g' "$target") || return 1
        [[ "$metadata" == 1000 ]] || use_docker_repair=true
        metadata=$(stat -c '%g' "$lock") || return 1
        [[ "$metadata" == 1000 ]] || use_docker_repair=true
        metadata=$(stat -c '%u' "$lock") || return 1
        [[ "$metadata" == "$(stat -c '%u' "$target")" && -r "$lock" ]] || use_docker_repair=true
        for private_store in "$target/pixel-chat-results" "$target/pixel-images"; do
            if [[ -d "$private_store" && ! -L "$private_store" ]]; then
                metadata=$(stat -c '%u:%g' "$private_store") || return 1
                [[ "$metadata" == 1000:1000 ]] || use_docker_repair=true
            fi
        done
        if [[ -d "$models" ]]; then
            metadata=$(stat -c '%g:%a' "$models") || return 1
            IFS=: read -r group mode <<< "$metadata"
            [[ "$group" == 1000 ]] && (( (8#$mode & 8#050) == 8#050 )) || use_docker_repair=true
        fi
    fi
    if "$use_docker_repair"; then
        # Host chgrp 1000 is not container GID 1000 in a rootless namespace.
        # The same pinned, offline helper also repairs rootful Docker when
        # the installer has Docker access but no passwordless sudo.
        _ods_rootless_ensure_helper_image || return 1
        _ods_dashboard_lock_helper_image || return 1
        docker_run run --rm --network none --user 0:0 \
            --mount "type=bind,src=$target,dst=/data" \
            "$ODS_ROOTLESS_HELPER_IMAGE" sh -ec '
                if test -e /data/models || test -L /data/models; then
                    test -d /data/models && test ! -L /data/models || exit 1
                fi
                chgrp 1000 /data
                chmod g+rwx /data
                test "$(stat -c %g /data)" = 1000
                mode=$(stat -c %a /data)
                test "$((0$mode & 070))" = 56
                for private_store in /data/pixel-chat-results /data/pixel-images; do
                    if test -e "$private_store" || test -L "$private_store"; then
                        test -d "$private_store" && test ! -L "$private_store" || exit 1
                        if test "$(stat -c %u:%g "$private_store")" != 1000:1000; then
                            chown -h -R 1000:1000 "$private_store"
                        fi
                        test "$(stat -c %u:%g "$private_store")" = 1000:1000
                    fi
                done
                if test -d /data/models; then
                    chgrp -h 1000 /data/models
                    chmod g+rx /data/models
                    test "$(stat -c %g /data/models)" = 1000
                    mode=$(stat -c %a /data/models)
                    test "$((0$mode & 050))" = 40
                fi
            ' || return 1
        docker_run run --rm -i --network none --user 0:0 --read-only \
            --security-opt no-new-privileges \
            --mount "type=bind,src=$target,dst=/data" \
            "$ODS_DASHBOARD_LOCK_IMAGE" python3 - repair /data < "$lock_helper"
        return $?
    fi
    metadata=$(stat -c '%g:%a' "$target") || return 1
    IFS=: read -r group mode <<< "$metadata"
    if [[ "$group" != 1000 ]]; then
        _ods_dashboard_mutate chgrp 1000 "$target" || return 1
    fi
    if (( (8#$mode & 8#070) != 8#070 )); then
        _ods_dashboard_mutate chmod g+rwx "$target" || return 1
    fi
    metadata=$(stat -c '%g:%a' "$target") || return 1
    IFS=: read -r group mode <<< "$metadata"
    [[ "$group" == 1000 ]] && (( (8#$mode & 8#070) == 8#070 )) || return 1
    if [[ -e "$models" || -L "$models" ]]; then
        [[ -d "$models" && ! -L "$models" ]] || return 1
        metadata=$(stat -c '%g:%a' "$models") || return 1
        IFS=: read -r group mode <<< "$metadata"
        if [[ "$group" != 1000 ]]; then
            _ods_dashboard_mutate chgrp -h 1000 "$models" || return 1
        fi
        if (( (8#$mode & 8#050) != 8#050 )); then
            _ods_dashboard_mutate chmod g+rx "$models" || return 1
        fi
        metadata=$(stat -c '%g:%a' "$models") || return 1
        IFS=: read -r group mode <<< "$metadata"
        [[ "$group" == 1000 ]] && (( (8#$mode & 8#050) == 8#050 )) || return 1
    fi
    for private_store in "$target/pixel-chat-results" "$target/pixel-images"; do
        if [[ -e "$private_store" || -L "$private_store" ]]; then
            [[ -d "$private_store" && ! -L "$private_store" ]] || return 1
            metadata=$(stat -c '%u:%g' "$private_store") || return 1
            if [[ "$metadata" != 1000:1000 ]]; then
                # Older generic reinstall repair transferred these private
                # trees to the installing user. Restore them without following
                # symlinks or changing modes, files, or other services' data.
                _ods_dashboard_mutate chown -h -R 1000:1000 "$private_store" || return 1
            fi
            [[ "$(stat -c '%u:%g' "$private_store")" == 1000:1000 ]] || return 1
        fi
    done
    # Ready locks need no elevation; ordinary owner/group authority may also
    # suffice. A failed repair must propagate if privileged repair is denied.
    python3 "$lock_helper" repair "$target" >/dev/null 2>&1 ||
        _ods_dashboard_mutate python3 "$lock_helper" repair "$target" >/dev/null
}
