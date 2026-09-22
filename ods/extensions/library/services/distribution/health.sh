#!/bin/sh
set -eu
exec curl --config /tmp/ods-registry/health.conf --fail --silent --show-error --max-time 5 --output /dev/null http://127.0.0.1:5000/v2/
