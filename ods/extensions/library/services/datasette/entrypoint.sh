#!/usr/bin/env bash
# Serve explicit SQLite snapshots; never auto-load plugins or metadata from data.
set -euo pipefail

set --
for database in /data/*.db /data/*.sqlite /data/*.sqlite3; do
    [ -f "$database" ] || continue
    set -- "$@" --immutable "$database"
done

exec datasette serve "$@" --host 0.0.0.0 --port 8001 --root \
    --metadata /ods-metadata.json \
    --setting allow_download off --setting sql_time_limit_ms 1000 \
    --setting max_returned_rows 1000 --setting max_csv_mb 10
