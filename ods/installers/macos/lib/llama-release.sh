#!/usr/bin/env bash
# Purpose: Track the llama.cpp release owned by the native macOS installer.
# Expects: None.
# Provides: macos_llama_release_is_current(), macos_record_llama_release().

macos_llama_release_is_current() {
    local binary="$1" stamp_file="$2" required_tag="$3" installed_tag=""
    [[ -x "$binary" && -f "$stamp_file" ]] || return 1
    IFS= read -r installed_tag < "$stamp_file" || return 1
    [[ "$installed_tag" == "$required_tag" ]]
}

macos_record_llama_release() {
    local stamp_file="$1" release_tag="$2"
    printf '%s\n' "$release_tag" > "${stamp_file}.tmp"
    mv "${stamp_file}.tmp" "$stamp_file"
}
