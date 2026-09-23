#!/usr/bin/env bash
# Verify a reviewed immutable artifact before any caller executes or extracts it.

ods_download_verified() {
    local url="$1" expected="$2" destination="$3" actual
    [[ "$url" == https://* && "$expected" =~ ^[0-9a-f]{64}$ ]] || return 1
    [[ -f "$destination" && ! -L "$destination" && -O "$destination" ]] || return 1
    chmod 600 "$destination" || return 1
    curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' \
        --max-time 300 --output "$destination" "$url" || return 1
    if command -v sha256sum >/dev/null 2>&1; then
        actual=$(sha256sum "$destination") || return 1
    elif command -v shasum >/dev/null 2>&1; then
        actual=$(shasum -a 256 "$destination") || return 1
    else
        printf '%s\n' '[ERROR] No SHA-256 verifier is available.' >&2
        return 1
    fi
    actual=${actual%% *}
    if [[ "$actual" != "$expected" ]]; then
        printf '%s\n' '[ERROR] Download checksum mismatch; artifact was not accepted.' >&2
        return 1
    fi
    printf '[verified-download] sha256=%s source=%s\n' "$actual" "$url"
}

ods_opencode_artifact() {
    local asset digest platform architecture libc=glibc
    platform=$(uname -s)
    architecture=$(uname -m)
    # Preserve upstream v1.2.18's Rosetta and libc selection. Use its baseline
    # target for every x64 CPU, including machines without AVX2.
    if [[ "$platform/$architecture" == Darwin/x86_64 ]] &&
        [[ "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" == 1 ]]; then
        architecture=arm64
    fi
    if [[ "$platform" == Linux ]]; then
        if [[ -f /etc/alpine-release ]] ||
            { command -v ldd >/dev/null 2>&1 && { ldd --version 2>&1 || true; } | grep -qi musl; }; then
            libc=musl
        fi
    fi
    case "$platform/$architecture/$libc" in
        Linux/x86_64/glibc)
            asset=opencode-linux-x64-baseline.tar.gz
            digest=55fd8ed686d4b897f2ff0972fac1ffc5e6d0632aec6f443e114aa93e84b720da ;;
        Linux/x86_64/musl)
            asset=opencode-linux-x64-baseline-musl.tar.gz
            digest=1a177993e137e4e71be94dc6bb3a2371fd6c461a2671c87b9151b1b054bb6940 ;;
        Linux/aarch64/glibc|Linux/arm64/glibc)
            asset=opencode-linux-arm64.tar.gz
            digest=cd8b3cd13bef12e29f32e0f32e5ca48e29159cf6cd32ddee8b3be97438a0242c ;;
        Linux/aarch64/musl|Linux/arm64/musl)
            asset=opencode-linux-arm64-musl.tar.gz
            digest=354b60969c70cecf1db7be9fc8e68ed4a52d2e6247c61bfa0e5ac862fff99502 ;;
        Darwin/arm64/glibc)
            asset=opencode-darwin-arm64.zip
            digest=7dc1eb25b79a85ec882df9560a2b1d84927a6f9981165a56f562ab704a312529 ;;
        Darwin/x86_64/glibc)
            asset=opencode-darwin-x64-baseline.zip
            digest=262e1cd86e6df5ec1f6199454429b1d04542dc3e9f30281ae8d53553d0623de7 ;;
        *) printf '%s\n' '[ERROR] No reviewed OpenCode artifact for this platform.' >&2; return 1 ;;
    esac
    printf '%s %s\n' "$asset" "$digest"
}

ods_install_verified_opencode() {
    local artifact asset digest stage candidate
    artifact=$(ods_opencode_artifact) || return 1
    read -r asset digest <<< "$artifact"
    stage=$(mktemp -d "${TMPDIR:-/tmp}/ods-opencode.XXXXXXXX") || return 1
    (umask 077; : > "$stage/$asset") || return 1
    if ! ods_download_verified "https://github.com/anomalyco/opencode/releases/download/v1.2.18/$asset" \
        "$digest" "$stage/$asset"; then
        rm -rf -- "$stage"
        return 1
    fi
    case "$asset" in
        *.zip) unzip -q "$stage/$asset" -d "$stage" ;;
        *) tar -xzf "$stage/$asset" -C "$stage" ;;
    esac || { rm -rf -- "$stage"; return 1; }
    candidate="$stage/opencode"
    if [[ ! -f "$candidate" || -L "$candidate" ]]; then
        printf '%s\n' '[ERROR] Reviewed OpenCode archive has no expected executable.' >&2
        rm -rf -- "$stage"
        return 1
    fi
    mkdir -p "$HOME/.opencode/bin" && install -m 755 "$candidate" "$HOME/.opencode/bin/opencode"
    local result=$?
    rm -rf -- "$stage"
    return "$result"
}
