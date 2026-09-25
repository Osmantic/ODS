#!/usr/bin/env bash

# Keep the Hermes bind mount private. A fresh, user-owned directory only needs
# chmod; an existing tree with foreign-owned entries still needs rootful repair.
ods_ensure_hermes_private_dir() {
    local directory="$1" owner_uid="$2" owner_gid="$3" metadata foreign_entry
    metadata=$(stat -c '%u:%g:%a' "$directory") || return 1
    [[ "$metadata" == "$owner_uid:$owner_gid:700" ]] && return 0

    if [[ "$owner_uid" == "$(id -u)" && "$metadata" == "$owner_uid:$owner_gid:"* ]]; then
        foreign_entry=$(find "$directory" -mindepth 1 \
            \( ! -uid "$owner_uid" -o ! -gid "$owner_gid" \) -print -quit) || return 1
        if [[ -z "$foreign_entry" ]]; then
            chmod 700 "$directory" || {
                error "Failed to preserve private mode 700 on data/hermes"
                return 1
            }
            return 0
        fi
    fi

    if ! ods_sudo_available; then
        error "Hermes requires data/hermes ownership $owner_uid:$owner_gid and mode 700 with a rootful runtime. Grant privileged access or disable Hermes, then re-run ODS."
        return 1
    fi
    ods_sudo chown -R "$owner_uid:$owner_gid" "$directory" 2>/dev/null || {
        error "Failed to restore data/hermes ownership to $owner_uid:$owner_gid"
        return 1
    }
    ods_sudo chmod 700 "$directory" 2>/dev/null || {
        error "Failed to preserve private mode 700 on data/hermes"
        return 1
    }
}
