#!/bin/sh
set -eu
curl --fail --silent --show-error --max-time 5 --output /dev/null http://127.0.0.1:8080/healthz/
exec /app/bin/health_check
