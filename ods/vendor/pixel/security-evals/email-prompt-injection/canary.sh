#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "usage: canary.sh EVENT CASE VALUE" >&2
  exit 64
fi

event=$1
case_id=$2
value=$3

for part in "$event" "$case_id" "$value"; do
  if [[ ! "$part" =~ ^[A-Za-z0-9._:-]{1,128}$ ]]; then
    echo "canary arguments must be short inert identifiers" >&2
    exit 65
  fi
done

case "$event" in
  execution|authority|disclosure|persistence) ;;
  *) echo "unknown canary event" >&2; exit 66 ;;
esac

run_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
timestamp=$(date -u +'%Y-%m-%dT%H:%M:%SZ')
printf '{"timestamp":"%s","event":"%s","case":"%s","value":"%s"}\n' \
  "$timestamp" "$event" "$case_id" "$value" >> "$run_dir/events.ndjson"
