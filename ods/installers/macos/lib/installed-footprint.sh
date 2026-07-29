#!/usr/bin/env bash

# Move obsolete root-level development files out of an installed tree without
# destroying user modifications. Callers must pass root entry names, not paths.
ods_quarantine_development_paths() {
    local install_dir="$1"
    shift

    local relative_path stale_path backup_parent backup_dir=""
    for relative_path in "$@"; do
        case "$relative_path" in
            ""|"."|".."|*/*)
                printf 'Invalid root-level development path: %s\n' "$relative_path" >&2
                return 1
                ;;
        esac

        stale_path="${install_dir}/${relative_path}"
        if [[ ! -e "$stale_path" && ! -L "$stale_path" ]]; then
            continue
        fi

        if [[ -z "$backup_dir" ]]; then
            backup_parent="${install_dir}/data/installer-backups/development-footprint"
            mkdir -p "$backup_parent"
            backup_dir="$(mktemp -d "${backup_parent}/$(date -u +%Y%m%d-%H%M%S).XXXXXX")"
        fi

        # mv operates on the directory entry itself. It does not traverse a
        # symlink, including a broken one, into content outside INSTALL_DIR.
        mv -- "$stale_path" "${backup_dir}/${relative_path}"
    done

    [[ -z "$backup_dir" ]] || printf '%s\n' "$backup_dir"
}
