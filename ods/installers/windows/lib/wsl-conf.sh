#!/bin/sh
# Update the selected WSL settings before Python or ODS is installed.
set -eu

path=$1
shift
case "$path" in /*) ;; *) echo 'wsl.conf path must be absolute' >&2; exit 1 ;; esac
[ "$#" -gt 0 ] && [ $(( $# % 3 )) -eq 0 ] || exit 1
[ ! -L "$path" ] || { echo 'Refusing a symlinked wsl.conf' >&2; exit 1; }
if [ -e "$path" ] && [ ! -f "$path" ]; then
    echo 'wsl.conf is not a regular file' >&2
    exit 1
fi
input=/dev/null
[ ! -e "$path" ] || input=$path
umask 022
temporary=$(mktemp "${path}.ods.XXXXXX")
trap 'rm -f -- "$temporary"' EXIT
trap 'exit 1' HUP INT TERM

awk '
function finish(section, i, pair) {
    for (i = 1; i <= count; i++) {
        pair = sections[i] SUBSEP keys[i]
        if (sections[i] == section && !written[pair]) {
            print keys[i] " = " values[pair]
            written[pair] = 1
        }
    }
}
BEGIN {
    for (i = 2; i < ARGC; i += 3) {
        sections[++count] = ARGV[i]
        keys[count] = ARGV[i + 1]
        values[ARGV[i] SUBSEP ARGV[i + 1]] = ARGV[i + 2]
        delete ARGV[i]; delete ARGV[i + 1]; delete ARGV[i + 2]
    }
}
{
    sub(/\r$/, "")
    if ($0 ~ /^[ \t]*\[[^][]+\]/) {
        finish(section)
        section = $0
        sub(/^[ \t]*\[/, "", section)
        sub(/\].*$/, "", section)
        encountered[section] = 1
    } else if ($0 ~ /^[ \t]*[^#;=]+=/) {
        key = $0
        sub(/=.*/, "", key)
        gsub(/^[ \t]+|[ \t]+$/, "", key)
        pair = section SUBSEP key
        if (pair in values) {
            if (!written[pair]) print key " = " values[pair]
            written[pair] = 1
            next
        }
    }
    print
}
END {
    finish(section)
    for (i = 1; i <= count; i++) {
        section = sections[i]
        if (!encountered[section]) {
            print "\n[" section "]"
            finish(section)
            encountered[section] = 1
        }
    }
}' "$input" "$@" > "$temporary"
if [ "$input" != /dev/null ]; then
    chmod --reference="$path" "$temporary"
    chown --reference="$path" "$temporary"
else
    chmod 644 "$temporary"
fi
mv -f -- "$temporary" "$path"
