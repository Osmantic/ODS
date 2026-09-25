#!/bin/sh
set -eu
export PGPASSWORD="$PGVECTOR_APP_PASSWORD"
export PGCONNECT_TIMEOUT=5
# TCP exercises password authentication; querying the type exercises pgvector.
result=$(psql -X -h 127.0.0.1 -U ods_app -d vectors -At -v ON_ERROR_STOP=1 \
  -c "SELECT extversion || ':' || ('[1,0]'::vector <-> '[1,0]'::vector)::text FROM pg_extension WHERE extname='vector'")
[ "$result" = '0.8.6:0' ]
