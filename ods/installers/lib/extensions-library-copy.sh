#!/usr/bin/env bash
# Copy public Extensions Library templates so the non-root Dashboard API can
# read them even when the installer was started with a restrictive umask.

ods_copy_extensions_library() {
    local source_dir="$1" data_dir="$2" target_dir="$2/extensions-library"
    local symlink

    [[ -d "$source_dir" && ! -L "$source_dir" && -d "$data_dir" && ! -L "$data_dir" ]] || return 1
    [[ ! -L "$target_dir" ]] || return 1
    [[ ! -e "$target_dir" || -d "$target_dir" ]] || return 1
    symlink="$(find -P "$source_dir" -type l -print -quit)" || return 1
    [[ -z "$symlink" ]] || return 1
    if [[ -d "$target_dir" ]]; then
        symlink="$(find -P "$target_dir" -type l -print -quit)" || return 1
        [[ -z "$symlink" ]] || return 1
    fi

    # Keep the caller's umask for secrets elsewhere in phase 06. These are
    # product-owned public templates, and the API may have a different UID.
    (umask 022; mkdir -p "$target_dir" && cp -r "$source_dir/." "$target_dir/") || return 1
    # Only traversal is needed on the parent data directory; do not expose
    # its other private child names merely to make the Library reachable.
    chmod go+x,go-w "$data_dir" || return 1
    chmod go+rx,go-w "$target_dir" || return 1

    # Only template-derived paths get their read/traverse bits repaired. A
    # retained custom entry outside the bundled template names is untouched.
    find -P "$source_dir" \( -type d -o -type f \) -exec bash -c '
        source_root=$1; target_root=$2; shift 2
        for source_path do
            target_path=$target_root${source_path#"$source_root"}
            [[ ! -L "$target_path" ]] || exit 1
            if [[ -d "$source_path" ]]; then
                [[ -d "$target_path" ]] && chmod go+rx,go-w "$target_path" || exit 1
            else
                [[ -f "$target_path" ]] && chmod go+rX,go-w "$target_path" || exit 1
            fi
        done
    ' _ "$source_dir" "$target_dir" {} + || return 1
    # Preserve the prior rule that no installed Library entry is writable by
    # another host user, including retained custom entries.
    find -P "$target_dir" \( -type d -o -type f \) -exec chmod go-w {} +
}
