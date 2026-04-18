#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app/results

cd /app/tests
exec pytest -v --tb=short --color=yes --timeout=60 "$@"
