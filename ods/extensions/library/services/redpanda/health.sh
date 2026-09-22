#!/bin/bash
set -euo pipefail
status=$(curl --fail --silent --show-error http://127.0.0.1:9644/v1/status/ready)
grep -q '"status"[[:space:]]*:[[:space:]]*"ready"' <<< "$status"
