#!/usr/bin/env bash
set -euo pipefail

python -m pytest tests/test_auth_router.py tests/test_csrf_origin.py -v
