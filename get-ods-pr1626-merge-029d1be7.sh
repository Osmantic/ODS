#!/usr/bin/env bash
set -euo pipefail

export ODS_REPO_URL="${ODS_REPO_URL:-https://github.com/Light-Heart-Labs/DreamServer.git}"
export ODS_REF="${ODS_REF:-codex/pr1626-merge-test-029d1be7}"
export ODS_BOOTSTRAP_REF="${ODS_BOOTSTRAP_REF:-$ODS_REF}"

bootstrap_url="https://raw.githubusercontent.com/Light-Heart-Labs/DreamServer/${ODS_BOOTSTRAP_REF}/ods/get-ods.sh"
curl -fsSL "$bootstrap_url" | bash -s -- "$@"
