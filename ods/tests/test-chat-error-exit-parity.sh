#!/usr/bin/env bash
# Contract: chat commands must return nonzero for transport and response errors.

set -euo pipefail

root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
linux_cli="$root_dir/ods-cli"
macos_cli="$root_dir/installers/macos/ods-macos.sh"
windows_cli="$root_dir/installers/windows/ods.ps1"

linux_chat="$(awk '/^cmd_chat\(\)/,/^}/' "$linux_cli")"
macos_chat="$(awk '/^cmd_chat\(\)/,/^}/' "$macos_cli")"
windows_chat="$(awk '/^function Invoke-Chat/,/^}/' "$windows_cli")"

grep -q "jq -er '.choices\[0\].message.content'" <<< "$linux_chat" || {
    printf '[FAIL] Linux chat no longer rejects error-only response payloads\n' >&2
    exit 1
}
grep -q "jq -er '.choices\[0\].message.content'" <<< "$macos_chat" || {
    printf '[FAIL] macOS chat does not reject error-only response payloads\n' >&2
    exit 1
}
grep -q 'Chat response did not contain assistant content' <<< "$windows_chat" || {
    printf '[FAIL] Windows chat accepts a response without assistant content\n' >&2
    exit 1
}

windows_catch="$(awk '
    /^function Invoke-Chat/ { in_chat=1 }
    in_chat && /} catch {/ { in_catch=1 }
    in_catch { print }
    in_catch && /^    }$/ { exit }
' "$windows_cli")"
grep -q 'exit 1' <<< "$windows_catch" || {
    printf '[FAIL] Windows chat swallows transport/response errors with exit 0\n' >&2
    exit 1
}

printf '[PASS] chat errors return nonzero on every platform\n'
