#!/bin/bash
set -euo pipefail
info=$(curl --fail --silent --show-error http://127.0.0.1:8080/v1/info)
grep -q '"starting":\s*false' <<< "$info"
