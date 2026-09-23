#!/usr/bin/env bash
# Only downloads/extracts reviewed archives. Never starts a binary or service.
# Return 10 only for a transport/HTTP failure eligible for the Homebrew fallback;
# 20 means configuration, integrity, staging or extraction failure (no fallback).
ods_install_verified_macos_llama() (
    local source_root="$1" tag="$2" binary="$3" artifact asset url digest stage temp_parent download_rc found found_dir target_dir cleanup_cmd
    local verifier="$source_root/installers/lib/native-llama-artifact.py"
    local manifest="$source_root/installers/native-llama-artifacts.json"
    local python_cmd="${ODS_PYTHON_CMD:-python3}"
    artifact=$("$python_cmd" "$verifier" --manifest "$manifest" --platform macos-arm64 --tag "$tag") || return 20
    IFS=$'\t' read -r asset url digest <<< "$artifact"
    [[ -n "$asset" && -n "$url" && "$digest" =~ ^[0-9a-f]{64}$ && "$binary" == /* ]] || return 20
    temp_parent=$(cd "${TMPDIR:-/tmp}" && pwd -P) || return 20
    umask 077
    stage=$(mktemp -d "$temp_parent/ods-native-llama.XXXXXXXX") || return 20
    [[ -d "$stage" && ! -L "$stage" && -O "$stage" ]] || return 20
    # Freeze the validated paths while the locals still exist; cleanup must
    # not depend on their lifetime when the Bash 3.2 subshell exits on failure.
    printf -v cleanup_cmd 'case %q in %q/ods-native-llama.*) rm -rf -- %q ;; esac' "$stage" "$temp_parent" "$stage"
    trap "$cleanup_cmd" EXIT
    chmod 700 "$stage" || return 20
    if curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --connect-timeout 30 --max-time 300 --output "$stage/$asset" "$url"; then
        :
    else
        download_rc=$?
        case "$download_rc" in
            6|7|18|22|28|52|55|56) return 10 ;;
            *) return 20 ;;
        esac
    fi
    "$python_cmd" "$verifier" --manifest "$manifest" --platform macos-arm64 --tag "$tag" \
        --verify "$stage/$asset" >/dev/null || return 20
    printf '[verified-download] sha256=%s source=%s\n' "$digest" "$url"
    mkdir "$stage/extract" || return 20
    tar xzf "$stage/$asset" -C "$stage/extract" || return 20
    found=$(find "$stage/extract" -name llama-server -type f -print -quit) || return 20
    [[ -n "$found" ]] || return 20
    found_dir=$(dirname "$found")
    target_dir=$(dirname "$binary")
    mkdir -p "$target_dir" || return 20
    cp "$found" "$binary" || return 20
    chmod +x "$binary" || return 20
    # Preserve the existing companion-library layout without running the binary.
    find "$found_dir" -name '*.dylib' -exec cp {} "$target_dir/" \; || return 20
    find "$found_dir" -name '*.metal' -exec cp {} "$target_dir/" \; || return 20
    xattr -rd com.apple.quarantine "$binary" 2>/dev/null || true
    xattr -rd com.apple.quarantine "$target_dir"/*.dylib 2>/dev/null || true
)
