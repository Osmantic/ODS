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

_ods_install_log_owner() {
    if [[ "$(uname -s)" == Darwin ]]; then stat -f '%u' "$1"
    else stat -c '%u' -- "$1"; fi
}

_ods_install_log_links() {
    if [[ "$(uname -s)" == Darwin ]]; then stat -f '%l' "$1"
    else stat -c '%h' -- "$1"; fi
}

ods_prepare_install_log() {
    local log_path="$1" existing_only="${2:-}" parent parent_mode file_mode ancestor owner links
    # An explicit discard sink is useful for source tests and cannot disclose
    # diagnostics to another reader.
    if [[ "$log_path" == /dev/null ]]; then
        ODS_PREPARED_INSTALL_LOG=/dev/null
        return 0
    fi
    if [[ "$existing_only" == legacy ]] \
        && [[ -L "$log_path" || ! -f "$log_path" || ! -O "$log_path" ]]; then
        return 1
    fi

    parent="$(dirname -- "$log_path")" || return 1
    if [[ ! -d "$parent" ]]; then
        printf '%s\n' '[ERROR] Installer log directory is missing or unsafe.' >&2
        return 1
    fi
    # Resolve trusted system aliases such as macOS /tmp -> /private/tmp, then
    # open through the resolved parent rather than through a mutable symlink.
    parent="$(cd -P -- "$parent" && pwd)" || return 1
    log_path="$parent/$(basename -- "$log_path")"
    # Reopening by pathname is safe only while another UID cannot replace its
    # file or any directory component. A sticky ancestor such as /tmp protects
    # our owned child directory, but cannot safely host a predictable log itself.
    ancestor="$parent"
    while :; do
        parent_mode="$(_ods_install_log_mode "$ancestor")" || return 1
        owner="$(_ods_install_log_owner "$ancestor")" || return 1
        if [[ ! "$parent_mode" =~ ^[0-7]{3,6}$ || ! "$owner" =~ ^[0-9]+$ ]] \
            || [[ "$owner" != 0 && "$owner" != "$EUID" ]] \
            || { (( (8#$parent_mode & 0022) != 0 )) \
                 && { [[ "$ancestor" == "$parent" && "$existing_only" != legacy ]] \
                      || (( (8#$parent_mode & 01000) == 0 )); }; }; then
            printf '%s\n' '[ERROR] Installer log path must have trusted owners and a non-shared parent.' >&2
            return 1
        fi
        [[ "$ancestor" == / ]] && break
        ancestor="$(dirname -- "$ancestor")"
    done

    if [[ -L "$log_path" ]]; then
        printf '%s\n' '[ERROR] Installer log cannot be a symlink.' >&2
        return 1
    fi
    if [[ -e "$log_path" ]]; then
        links="$(_ods_install_log_links "$log_path")" || return 1
        if [[ ! -f "$log_path" || ! -O "$log_path" || "$links" != 1 ]] || ! chmod 600 "$log_path"; then
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
    ODS_PREPARED_INSTALL_LOG="$log_path"
}

# Update the caller's actual variable so later tee/redirections cannot reopen a
# mutable parent alias. Empty defaults are allocated only after argument parsing.
ods_prepare_install_log_var() {
    local variable="$1" legacy="${2:-}" requested private_dir
    [[ "$variable" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || return 1
    requested="${!variable-}"
    if [[ -z "$requested" ]]; then
        # Retire the old shared filename without deleting diagnostic history or
        # following somebody else's link/file. Sticky directories protect an
        # already owned file during this one-time permission repair.
        if [[ -n "$legacy" && ( -e "$legacy" || -L "$legacy" ) ]]; then
            if ! ods_prepare_install_log "$legacy" legacy; then
                printf '%s\n' '[WARN] Unsafe legacy installer log left untouched; new diagnostics use a private directory.' >&2
            fi
        fi
        private_dir="$(umask 077; mktemp -d "${TMPDIR:-/tmp}/ods-install.XXXXXXXX")" || return 1
        requested="$private_dir/install.log"
    fi
    ods_prepare_install_log "$requested" || return 1
    printf -v "$variable" '%s' "$ODS_PREPARED_INSTALL_LOG"
}
