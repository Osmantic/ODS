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
expected_command='dir="$1"; shift; rm -rf "$dir" && mkdir -p -m 0700 "$dir" && export BUN_TMPDIR="$dir" && exec "$@"'
mock_plist_args=(/bin/sh -c "$expected_command" ods-opencode-web "$bun_tmp"
    "$scratch/.opencode/bin/opencode" web --port 3003 --hostname 127.0.0.1)
mock_loaded_args=("${mock_plist_args[@]}")
mock_disabled=enabled
mock_loaded=true
mock_loaded_plist="$plist"
mock_loaded_program=/bin/sh

ods_macos_opencode_plist_value() {
    case "$2" in
        Label) printf '%s\n' "$mock_label" ;;
        ProgramArguments:*)
            local index="${2#ProgramArguments:}"
            [[ "$index" =~ ^[0-9]+$ ]] && (( index < ${#mock_plist_args[@]} )) || return 1
            printf '%s\n' "${mock_plist_args[$index]}" ;;
        *) return 1 ;;
    esac
}
launchctl() {
    case "$1" in
        print-disabled) printf '\t"%s" => %s\n' "$label" "$mock_disabled" ;;
        print)
            "$mock_loaded" || return 1
            printf '\tpath = %s\n\tprogram = %s\n\targuments = {\n' \
                "$mock_loaded_plist" "$mock_loaded_program"
            printf '\t\t%s\n' "${mock_loaded_args[@]}"
            printf '\t}\n'
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
mock_plist_args[5]="$scratch/other-app"
if ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp"; then
    echo 'FAIL: foreign binary was accepted' >&2; exit 1
fi
mock_plist_args[5]="$scratch/.opencode/bin/opencode"
mock_plist_args[2]='echo foreign command'
if ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp"; then
    echo 'FAIL: foreign shell command was accepted' >&2; exit 1
fi
mock_plist_args[2]="$expected_command"

mock_plist_args[10]='0.0.0.0'
if ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp"; then
    echo 'FAIL: foreign hostname was accepted' >&2; exit 1
fi
mock_plist_args[10]='127.0.0.1'

mock_plist_args+=(--foreign-option)
if ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp"; then
    echo 'FAIL: extra plist argument was accepted' >&2; exit 1
fi
unset 'mock_plist_args[11]'

mock_loaded_args[2]='echo foreign command'
if ods_macos_opencode_loaded_owned "$plist" "$label" "$bun_tmp" 501; then
    echo 'FAIL: foreign loaded shell command was accepted' >&2; exit 1
fi
mock_loaded_args[2]="$expected_command"

mock_loaded_args[10]='0.0.0.0'
if ods_macos_opencode_loaded_owned "$plist" "$label" "$bun_tmp" 501; then
    echo 'FAIL: foreign loaded hostname was accepted' >&2; exit 1
fi
mock_loaded_args[10]='127.0.0.1'

mock_loaded_args+=(--foreign-option)
if ods_macos_opencode_loaded_owned "$plist" "$label" "$bun_tmp" 501; then
    echo 'FAIL: extra loaded argument was accepted' >&2; exit 1
fi
unset 'mock_loaded_args[11]'

ln -s "$plist" "$scratch/linked.plist"
if ods_macos_opencode_plist_owned "$scratch/linked.plist" "$label" "$bun_tmp"; then
    echo 'FAIL: symlink plist was accepted' >&2; exit 1
fi

echo 'PASS: Mac OpenCode selection follows owned, loaded, enabled agent state'
