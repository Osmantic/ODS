#!/usr/bin/env bash
# Purpose: Read-only formatting primitives shared by the ODS shell CLI.
# Expects: RED, GREEN, YELLOW (empty strings are valid for NO_COLOR output).
# Provides: _status_color, _json_escape, hr.
# Modder notes: Keep these helpers side-effect free; callers rely on command
# substitution and byte-stable JSON/table output.

# Map cmd_list status strings to colors. Unknown future states stay uncolored.
_status_color() {
    case "$1" in
        enabled|always-on) echo "$GREEN" ;;
        disabled|stopped)  echo "$YELLOW" ;;
        unhealthy|error)   echo "$RED" ;;
        *)                 echo "" ;;
    esac
}

_json_escape() {
    local s="${1-}"
    s=${s//\\/\\\\}
    s=${s//\"/\\\"}
    s=${s//$'\b'/\\b}
    s=${s//$'\f'/\\f}
    s=${s//$'\n'/\\n}
    s=${s//$'\r'/\\r}
    s=${s//$'\t'/\\t}
    printf '%s' "$s"
}

# Print a separator of repeated characters filling the given width.
# Usage: hr <width> [<char=─>]
hr() {
    local w="${1:-10}" c="${2:-─}" out=''
    printf -v out '%*s' "$w" ''
    printf '%s' "${out// /$c}"
}
