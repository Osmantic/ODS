#!/usr/bin/env bash
# Download native executable assets in private storage and verify before use.

macos_llama_asset_sha256() {
    # Independently hashed release assets; keep pins paired with tier-map releases.
    # llama-<tag>-bin-macos-arm64.tar.gz from github.com/ggml-org/llama.cpp
    # releases. Each digest matches the asset digest GitHub publishes for the
    # release and a separate download hashed with sha256sum.
    case "$1" in
        # Previous default pin, kept so rolling constants.sh back needs no new digest.
        b8210) printf '%s\n' '8cc228499f05adb69b92462f8060448bec75a7ba406f03c1fca8e628b4ff5c91' ;;
        # Default pin (constants.sh); tag commit d4b0c22f9e67, 8,593,054 bytes.
        b9014) printf '%s\n' '565aecda0838daa433f363ae9a1c9ed6c94831de3fd093cb08179a5a3fd4f22d' ;;
        *) ai_err "No trusted checksum for llama.cpp release $1."; return 1 ;;
    esac
}

_macos_verify_runtime_archive() {
    local archive="$1" expected="$2" actual
    # Unlike optional model checks, executable verification must fail closed.
    if [[ ! "$expected" =~ ^[a-f0-9]{64}$ || ! -f "$archive" || -L "$archive" ]]; then
        ai_err "Missing trusted checksum or regular llama-server archive."
        return 1
    fi
    if command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$archive")" || return 1
    elif command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$archive")" || return 1
    else
        ai_err "A SHA-256 tool is required to verify llama-server."
        return 1
    fi
    actual="${actual%%[[:space:]]*}"
    if [[ "$actual" != "$expected" ]]; then
        ai_err "llama-server archive checksum mismatch; refusing to install."
        return 1
    fi
}

macos_install_native_llama() (
    # Subshell traps clean only this invocation's directories, without replacing
    # caller traps. Do not use shared archive caches, including old .part files.
    local binary="$1" release="$2" asset="$3" url="$4"
    local destination expected temporary archive extracted found found_dir brew_binary
    if [[ -x "$binary" ]]; then
        ai_ok "llama-server already present"
        return 0
    fi
    [[ "$asset" == "llama-${release}-bin-macos-arm64.tar.gz" ]] || {
        ai_err "Unexpected llama-server asset name."
        return 1
    }
    expected="$(macos_llama_asset_sha256 "$release")" || return 1
    temporary="$(umask 077; mktemp -d /tmp/ods-llama.XXXXXXXXXX)" || return 1
    trap 'rm -rf -- "$temporary"' EXIT
    # Bash 3.2 can skip EXIT when a signal trap exits a function subshell.
    # Clean directly in each handler as well as on ordinary exit.
    trap 'trap - EXIT; rm -rf -- "$temporary"; exit 129' HUP
    trap 'trap - EXIT; rm -rf -- "$temporary"; exit 130' INT
    trap 'trap - EXIT; rm -rf -- "$temporary"; exit 143' TERM
    archive="$temporary/$asset"
    destination="$(dirname "$binary")"

    if download_with_progress "$url" "$archive" "Downloading llama-server (Metal)"; then
        # Never fall back to another route after an integrity failure.
        _macos_verify_runtime_archive "$archive" "$expected" || return 1
        extracted="$(mktemp -d "$temporary/extract.XXXXXXXXXX")" || return 1
        ai "Extracting llama-server..."
        tar xzf "$archive" -C "$extracted" || return 1
        found="$(find "$extracted" -name llama-server -type f -print -quit)" || return 1
        if [[ -z "$found" ]]; then
            ai_err "llama-server binary not found in verified archive."
            return 1
        fi
        mkdir -p "$destination" || return 1
        cp "$found" "$binary" || return 1
        chmod +x "$binary" || return 1
        found_dir="$(dirname "$found")"
        find "$found_dir" -name '*.dylib' -exec cp {} "$destination/" \; 2>/dev/null || true
        find "$found_dir" -name '*.metal' -exec cp {} "$destination/" \; 2>/dev/null || true
        ai_ok "Extracted verified llama-server"
    else
        # Preserve the separate Homebrew route for transport failures only.
        ai_warn "Pre-built binary download failed. Trying Homebrew..."
        if ! command -v brew >/dev/null 2>&1; then
            ai_err "llama-server download failed and Homebrew is not available."
            return 1
        fi
        (set -o pipefail; brew install llama.cpp 2>&1 | tail -5) || return 1
        brew_binary="$(command -v llama-server)" || return 1
        mkdir -p "$destination" || return 1
        cp "$brew_binary" "$binary" || return 1
        chmod +x "$binary" || return 1
        ai_ok "Installed llama-server via Homebrew"
    fi
    # Only verified release bytes or a successful Homebrew install reach here.
    xattr -rd com.apple.quarantine "$binary" 2>/dev/null || true
    xattr -rd com.apple.quarantine "$destination"/*.dylib 2>/dev/null || true
)
