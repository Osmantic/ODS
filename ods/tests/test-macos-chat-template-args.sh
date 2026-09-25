#!/usr/bin/env bash
# The native macOS launchers map .env's LLAMA_ARG_CHAT_TEMPLATE_FILE (a path
# inside the Linux llama-server containers) to the shipped template in the
# install, and ignore anything else. Platform-neutral: no llama-server runs.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
source "$ROOT_DIR/installers/macos/lib/native-model.sh"
read_env_value() { sed -n "s/^$2=//p" "$1" | head -1; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

INSTALL="$TMP_DIR/install dir"
mkdir -p "$INSTALL/config/llama-server/templates"
cp "$ROOT_DIR/config/llama-server/templates/qwen3.5-small-preserve-thinking.jinja" "$INSTALL/config/llama-server/templates/"

check() {
    local value="$1" expected="$2"
    printf 'GGUF_FILE=Qwen3.5-9B-Q4_K_M.gguf\n' > "$INSTALL/.env"
    [[ -z "$value" ]] || printf 'LLAMA_ARG_CHAT_TEMPLATE_FILE=%s\n' "$value" >> "$INSTALL/.env"
    macos_resolve_chat_template_args "$INSTALL" 2>/dev/null
    [[ "${MACOS_NATIVE_CHAT_TEMPLATE_ARGS[*]-}" == "$expected" ]] \
        || fail "'$value' -> '${MACOS_NATIVE_CHAT_TEMPLATE_ARGS[*]-}', expected '$expected'"
}

check "/config/llama-server/templates/qwen3.5-small-preserve-thinking.jinja" \
    "--chat-template-file $INSTALL/config/llama-server/templates/qwen3.5-small-preserve-thinking.jinja"
check "" ""
check "/config/llama-server/templates/missing.jinja" ""
check "/config/llama-server/templates/../templates/qwen3.5-small-preserve-thinking.jinja" ""
check "$INSTALL/config/llama-server/templates/qwen3.5-small-preserve-thinking.jinja" ""
check "/config/llama-server/templates/qwen3.5-small-preserve-thinking.jinja.bak" ""

# Both native launchers append it to the non-profile argument list.
for launcher in installers/macos/ods-macos.sh installers/macos/install-macos.sh; do
    grep -q 'macos_resolve_chat_template_args "$INSTALL_DIR"' "$ROOT_DIR/$launcher" || fail "$launcher does not resolve the chat template"
    grep -q 'MACOS_NATIVE_CHAT_TEMPLATE_ARGS\[@\]' "$ROOT_DIR/$launcher" || fail "$launcher does not pass the chat template"
done
echo "[PASS] native macOS chat template arguments"
