#!/usr/bin/env bash
# Read only the ODS OpenCode LaunchAgent contract. A binary or plist by itself
# does not mean OpenCode was selected, and a foreign plist must not be replaced.

ods_macos_opencode_plist_value() {
    local plist="$1" key="$2"
    "${ODS_MACOS_PLISTBUDDY:-/usr/libexec/PlistBuddy}" -c "Print :$key" "$plist" 2>/dev/null
}

ods_macos_opencode_expected_args() {
    local bun_tmp="$1" bin="$2"
    printf '%s\n' \
        /bin/sh -c \
        'dir="$1"; shift; rm -rf "$dir" && mkdir -p -m 0700 "$dir" && export BUN_TMPDIR="$dir" && exec "$@"' \
        ods-opencode-web "$bun_tmp" "$bin" web --port 3003 --hostname 127.0.0.1
}

ods_macos_opencode_plist_owned() {
    local plist="$1" label="$2" bun_tmp="$3" bin expected actual arg i
    [[ -f "$plist" && ! -L "$plist" ]] || return 1
    [[ "$(ods_macos_opencode_plist_value "$plist" Label)" == "$label" ]] || return 1
    bin="$(ods_macos_opencode_plist_value "$plist" ProgramArguments:5)" || return 1
    [[ "$bin" == /*/opencode ]] || return 1
    expected="$(ods_macos_opencode_expected_args "$bun_tmp" "$bin")"
    actual=""
    for ((i=0; i<11; i++)); do
        arg="$(ods_macos_opencode_plist_value "$plist" "ProgramArguments:$i")" || return 1
        if (( i == 0 )); then actual="$arg"; else actual="$actual"$'\n'"$arg"; fi
    done
    # An extra argument can change the service even when the prefix matches.
    if ods_macos_opencode_plist_value "$plist" ProgramArguments:11 >/dev/null; then return 1; fi
    [[ "$actual" == "$expected" ]]
}

ods_macos_opencode_disabled() {
    local label="$1" uid="$2" output
    # If launchd cannot report overrides, preserve the current process and
    # avoid silently selecting a new install on an uncertain rerun.
    output="$(launchctl print-disabled "gui/$uid" 2>/dev/null)" || return 0
    printf '%s\n' "$output" | awk -v target="\"$label\"" \
        '$1 == target && $2 == "=>" && $3 == "disabled" { found=1 } END { exit !found }'
}

ods_macos_opencode_loaded_owned() {
    local plist="$1" label="$2" bun_tmp="$3" uid="$4" output bin actual expected
    ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp" || return 1
    bin="$(ods_macos_opencode_plist_value "$plist" ProgramArguments:5)" || return 1
    output="$(launchctl print "gui/$uid/$label" 2>/dev/null)" || return 1
    printf '%s\n' "$output" | awk -v plist="$plist" '
        {
            line=$0
            sub(/^[ \t]+/, "", line)
            if (line == "arguments = {") exit
            if (line == "path = " plist) path=1
            if (line == "program = /bin/sh") program=1
        }
        END { exit !(path && program) }
    ' || return 1
    actual="$(printf '%s\n' "$output" | awk '
        /^[ \t]*arguments = \{$/ { inside=1; opened=1; next }
        inside && /^[ \t]*\}$/ { closed=1; exit }
        inside { sub(/^[ \t]+/, "", $0); print }
        END { if (!opened || !closed) exit 1 }
    ')" || return 1
    expected="$(ods_macos_opencode_expected_args "$bun_tmp" "$bin")"
    [[ "$actual" == "$expected" ]]
}

ods_macos_opencode_retained() {
    local plist="$1" label="$2" bun_tmp="$3" uid="$4"
    ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp" || return 1
    ods_macos_opencode_disabled "$label" "$uid" && return 1
    ods_macos_opencode_loaded_owned "$plist" "$label" "$bun_tmp" "$uid"
}
