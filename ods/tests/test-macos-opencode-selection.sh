#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=../installers/macos/lib/opencode-selection.sh
source "$root/installers/macos/lib/opencode-selection.sh"
scratch="$(mktemp -d)"
trap 'rm -rf -- "$scratch"' EXIT
plist="$scratch/com.ods.opencode-web.plist"
touch "$plist"
label=com.ods.opencode-web
bun_tmp="$scratch/opencode-bun-tmp"
mock_label="$label"
mock_arg5="$scratch/.opencode/bin/opencode"
mock_disabled=enabled
mock_loaded=true
mock_loaded_plist="$plist"
mock_loaded_program=/bin/sh

ods_macos_opencode_plist_value() {
    case "$2" in
        Label) printf '%s\n' "$mock_label" ;;
        ProgramArguments:0) printf '%s\n' /bin/sh ;;
        ProgramArguments:1) printf '%s\n' -c ;;
        ProgramArguments:3) printf '%s\n' ods-opencode-web ;;
        ProgramArguments:4) printf '%s\n' "$bun_tmp" ;;
        ProgramArguments:5) printf '%s\n' "$mock_arg5" ;;
        *) return 1 ;;
    esac
}
launchctl() {
    case "$1" in
        print-disabled) printf '\t"%s" => %s\n' "$label" "$mock_disabled" ;;
        print)
            "$mock_loaded" || return 1
            printf '\tpath = %s\n\tprogram = %s\n\targuments = {\n\t\tods-opencode-web\n\t\t%s\n\t\t%s\n\t}\n' \
                "$mock_loaded_plist" "$mock_loaded_program" "$bun_tmp" "$mock_arg5"
            ;;
        *) return 1 ;;
    esac
}

ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp" \
    || { echo 'FAIL: installed ODS plist was not recognized' >&2; exit 1; }
ods_macos_opencode_retained "$plist" "$label" "$bun_tmp" 501 \
    || { echo 'FAIL: loaded ODS agent was not retained' >&2; exit 1; }

mock_disabled=disabled
if ods_macos_opencode_retained "$plist" "$label" "$bun_tmp" 501; then
    echo 'FAIL: disabled ODS agent was reselected' >&2; exit 1
fi
mock_disabled=enabled
mock_loaded=false
if ods_macos_opencode_retained "$plist" "$label" "$bun_tmp" 501; then
    echo 'FAIL: unloaded ODS agent was reselected' >&2; exit 1
fi
mock_loaded=true
mock_loaded_plist="$scratch/foreign.plist"
if ods_macos_opencode_retained "$plist" "$label" "$bun_tmp" 501; then
    echo 'FAIL: foreign loaded service was reselected' >&2; exit 1
fi
mock_loaded_plist="$plist"
mock_loaded_program=/bin/false
if ods_macos_opencode_loaded_owned "$plist" "$label" "$bun_tmp" 501; then
    echo 'FAIL: foreign loaded program was accepted' >&2; exit 1
fi
mock_loaded_program=/bin/sh
mock_label=foreign.agent
if ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp"; then
    echo 'FAIL: foreign plist was accepted' >&2; exit 1
fi
mock_label="$label"
mock_arg5="$scratch/other-app"
if ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp"; then
    echo 'FAIL: foreign binary was accepted' >&2; exit 1
fi
mock_arg5="$scratch/.opencode/bin/opencode"
ln -s "$plist" "$scratch/linked.plist"
if ods_macos_opencode_plist_owned "$scratch/linked.plist" "$label" "$bun_tmp"; then
    echo 'FAIL: symlink plist was accepted' >&2; exit 1
fi

echo 'PASS: Mac OpenCode selection follows owned, loaded, enabled agent state'
