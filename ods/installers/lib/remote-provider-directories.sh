#!/usr/bin/env bash
# Remote-provider containers receive the installation data group, not the
# installation owner's UID. Their private state therefore needs group traversal.
#
# Prints what is wrong and returns 1 instead of stopping: the remote provider is
# optional, so callers warn and let the install continue. The folders are
# created as before; only the permission change refuses links and files. It
# only adds group read and traverse (g+rx) and never removes a bit, because
# rootless Docker and the Dashboard API reach the routing state through the
# other bits.
ods_prepare_remote_provider_directories() {
    local install_dir="$1" directory
    if ! mkdir -p "$install_dir/data/remote-provider/secrets"; then
        printf 'Could not create %s\n' "$install_dir/data/remote-provider/secrets" >&2
        return 1
    fi
    for directory in "$install_dir/data" "$install_dir/data/remote-provider" \
        "$install_dir/data/remote-provider/secrets"; do
        if [[ -L "$directory" || ! -d "$directory" ]]; then
            printf 'Refusing to change permissions through a link or a file: %s\n' "$directory" >&2
            return 1
        fi
    done
    for directory in "$install_dir/data/remote-provider" "$install_dir/data/remote-provider/secrets"; do
        if ! { chgrp "$(id -g)" "$directory" && chmod g+rx "$directory"; }; then
            printf 'Could not give the remote provider group access to %s\n' "$directory" >&2
            return 1
        fi
    done
}
