#!/usr/bin/env bash
# Read only the ODS OpenCode LaunchAgent contract. A binary or plist by itself
# does not mean OpenCode was selected, and a foreign plist must not be replaced.

ods_macos_opencode_plist_value() {
    local plist="$1" key="$2"
    "${ODS_MACOS_PLISTBUDDY:-/usr/libexec/PlistBuddy}" -c "Print :$key" "$plist" 2>/dev/null
}

ods_macos_opencode_plist_owned() {
    local plist="$1" label="$2" bun_tmp="$3" arg5
    [[ -f "$plist" && ! -L "$plist" ]] || return 1
    [[ "$(ods_macos_opencode_plist_value "$plist" Label)" == "$label" ]] || return 1
    [[ "$(ods_macos_opencode_plist_value "$plist" ProgramArguments:0)" == /bin/sh ]] || return 1
    [[ "$(ods_macos_opencode_plist_value "$plist" ProgramArguments:1)" == -c ]] || return 1
    [[ "$(ods_macos_opencode_plist_value "$plist" ProgramArguments:3)" == ods-opencode-web ]] || return 1
    [[ "$(ods_macos_opencode_plist_value "$plist" ProgramArguments:4)" == "$bun_tmp" ]] || return 1
    arg5="$(ods_macos_opencode_plist_value "$plist" ProgramArguments:5)" || return 1
    [[ "$arg5" == /*/opencode ]]
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
    local plist="$1" label="$2" bun_tmp="$3" uid="$4" output bin
    ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp" || return 1
    bin="$(ods_macos_opencode_plist_value "$plist" ProgramArguments:5)" || return 1
    output="$(launchctl print "gui/$uid/$label" 2>/dev/null)" || return 1
    printf '%s\n' "$output" | awk -v plist="$plist" -v bun_tmp="$bun_tmp" -v bin="$bin" '
        {
            line=$0
            sub(/^[ \t]+/, "", line)
            if (line == "path = " plist) path=1
            if (line == "program = /bin/sh") program=1
            if (line == "ods-opencode-web") marker=1
            if (line == bun_tmp) tmp=1
            if (line == bin) binary=1
        }
        END { exit !(path && program && marker && tmp && binary) }
    '
}

ods_macos_opencode_retained() {
    local plist="$1" label="$2" bun_tmp="$3" uid="$4"
    ods_macos_opencode_plist_owned "$plist" "$label" "$bun_tmp" || return 1
    ods_macos_opencode_disabled "$label" "$uid" && return 1
    ods_macos_opencode_loaded_owned "$plist" "$label" "$bun_tmp" "$uid"
}
