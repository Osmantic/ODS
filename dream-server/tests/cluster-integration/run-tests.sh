#!/usr/bin/env bash
set -euo pipefail

cd /app/tests
exec pytest -v --tb=short --color=yes --timeout=300 "$@"
