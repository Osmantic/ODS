#!/bin/sh
set -eu

if [ "$#" -eq 4 ] && [ "$1" = "--noprofile" ] && [ "$2" = "--norc" ] && [ "$3" = "-c" ]; then
    exec /bin/bash --noprofile --norc -o pipefail -c "$4"
fi

if [ "$#" -eq 3 ] && [ "$1" = "-i" ] && [ "$2" = "-c" ]; then
    exec /bin/bash --noprofile -o pipefail -i -c "$3"
fi

printf '%s\n' 'pixel exec shell: unsupported invocation' >&2
exit 64
