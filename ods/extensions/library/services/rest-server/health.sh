#!/bin/sh
set -eu
# Read only: an absent repository is normal before its owner initializes it.
status=$(curl --silent --show-error --max-time 5 --config /tmp/health-curl.conf --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8000/ods/health/config)
case "$status" in
    200|404) exit 0 ;;
    *) exit 1 ;;
esac
