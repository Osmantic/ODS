#!/usr/bin/env bash
# Prepare the Linux/macOS installer's diagnostic log before any message can append.

_ods_install_log_mode() {
    if [[ "$(uname -s)" == Darwin ]]; then
        # %p includes special permission bits; %Lp omits the sticky bit on macOS.
        stat -f '%p' "$1"
    else
        stat -c '%a' -- "$1"
    fi
}

ods_prepare_install_log() {
    local log_path="$1" parent parent_mode file_mode
    # An explicit discard sink is useful for source tests and cannot disclose
    # diagnostics to another reader.
    [[ "$log_path" == /dev/null ]] && return 0

    parent="$(dirname -- "$log_path")" || return 1
    if [[ ! -d "$parent" ]]; then
        printf '%s\n' '[ERROR] Installer log directory is missing or unsafe.' >&2
        return 1
    fi
    # Resolve trusted system aliases such as macOS /tmp -> /private/tmp, then
    # open through the resolved parent rather than through a mutable symlink.
    parent="$(cd -P -- "$parent" && pwd)" || return 1
    log_path="$parent/$(basename -- "$log_path")"
    parent_mode="$(_ods_install_log_mode "$parent")" || return 1
    if [[ ! "$parent_mode" =~ ^[0-7]{3,6}$ ]] \
        || (( (8#$parent_mode & 0022) != 0 && (8#$parent_mode & 01000) == 0 )); then
        printf '%s\n' '[ERROR] Installer log directory is writable by others without sticky protection.' >&2
        return 1
    fi

    if [[ -L "$log_path" ]]; then
        printf '%s\n' '[ERROR] Installer log cannot be a symlink.' >&2
        return 1
    fi
    if [[ -e "$log_path" ]]; then
        if [[ ! -f "$log_path" || ! -O "$log_path" ]] || ! chmod 600 "$log_path"; then
            printf '%s\n' '[ERROR] Installer log must be an owned regular file.' >&2
            return 1
        fi
    elif ! ( umask 077; set -C; : > "$log_path" ); then
        # noclobber uses exclusive creation, so a competing path cannot be
        # silently replaced or followed during the first open.
        printf '%s\n' '[ERROR] Could not create a private installer log.' >&2
        return 1
    fi

    if [[ -L "$log_path" || ! -f "$log_path" || ! -O "$log_path" ]]; then
        printf '%s\n' '[ERROR] Installer log ownership changed unexpectedly.' >&2
        return 1
    fi
    file_mode="$(_ods_install_log_mode "$log_path")" || return 1
    if [[ ! "$file_mode" =~ ^[0-7]{3,6}$ ]] || (( (8#$file_mode & 07777) != 0600 )); then
        printf '%s\n' '[ERROR] Installer log is not private.' >&2
        return 1
    fi
}
