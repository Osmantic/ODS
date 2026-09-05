#!/bin/bash
# ============================================================================
# ODS Installer — CLI link management
# ============================================================================
# Part of: installers/lib/
# Purpose: Keep the global/user `ods` command bound to the active install.
#
# Expects: ods_sudo(), ods_sudo_available()
# Provides: ods_cli_path_matches_install(), ods_bind_cli_command()
# ============================================================================

ods_cli_path_matches_install() {
    local candidate="$1" install_cli="$2" candidate_real install_real
    [[ -n "$candidate" && -n "$install_cli" ]] || return 1
    [[ ( -e "$candidate" || -L "$candidate" ) && -e "$install_cli" ]] || return 1
    candidate_real="$(readlink -f -- "$candidate" 2>/dev/null)" || return 1
    install_real="$(readlink -f -- "$install_cli" 2>/dev/null)" || return 1
    [[ -n "$candidate_real" && "$candidate_real" == "$install_real" ]]
}

_ods_cli_replace_link() {
    local target="$1" link="$2" privileged="$3"
    [[ -n "$target" && -n "$link" ]] || return 1

    # Never overwrite an unrelated regular file or directory. ODS only owns
    # the conventional command path when it is absent or already a symlink.
    if [[ -e "$link" && ! -L "$link" ]]; then
        return 1
    fi

    if [[ "$privileged" == "true" ]]; then
        ods_sudo_available || return 1
        ods_sudo ln -sfn -- "$target" "$link" || return 1
    else
        ln -sfn -- "$target" "$link" || return 1
    fi
    ods_cli_path_matches_install "$link" "$target"
}

ods_bind_cli_command() {
    local install_dir="$1" owner_home="$2"
    local install_cli="$install_dir/ods-cli"
    local system_link="${ODS_CLI_SYSTEM_LINK:-/usr/local/bin/ods}"
    local user_bin="$owner_home/.local/bin" user_link="$owner_home/.local/bin/ods"
    local current=""

    [[ "$install_dir" == /* && "$owner_home" == /* && "$system_link" == /* ]] || return 1
    [[ -f "$install_cli" && ! -L "$install_cli" && -x "$install_cli" ]] || return 1

    current="$(command -v ods 2>/dev/null || true)"
    if ods_cli_path_matches_install "$current" "$install_cli"; then
        printf 'existing:%s\n' "$current"
        return 0
    fi

    if _ods_cli_replace_link "$install_cli" "$system_link" true; then
        printf 'system:%s\n' "$system_link"
        return 0
    fi

    # Rootless fallback. Refuse a symlinked ~/.local/bin directory so an
    # attacker-controlled redirect cannot turn this into an arbitrary write.
    if [[ -e "$user_bin" || -L "$user_bin" ]]; then
        [[ -d "$user_bin" && ! -L "$user_bin" ]] || return 1
        [[ "$(stat -c '%u' -- "$user_bin" 2>/dev/null)" == "$(id -u)" ]] || return 1
    else
        install -d -m 0700 -- "$user_bin" || return 1
    fi
    if _ods_cli_replace_link "$install_cli" "$user_link" false; then
        printf 'user:%s\n' "$user_link"
        return 0
    fi
    return 1
}
