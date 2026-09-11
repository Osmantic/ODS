#!/bin/bash
set -euo pipefail

# Mock docker compose to fail on 'up -d'
docker() {
    if [[ \"$2\" == \"compose\" && \"$3\" == \"up\" ]]; then
        echo \"Error: simulated compose failure\"
        return 1
    fi
    command docker \"$@\"
}
export -f docker

# Setup dummy snapshot
mkdir -p test_snap
echo '{\"version\":\"1.0\"}' > test_snap/snapshot.json

# Run rollback and verify abort
if bash ods/ods-update.sh rollback test_snap 2>&1 | grep -q \"Rollback failed: docker compose up failed\"; then
    echo \"SUCCESS: Rollback reported failure as expected\"
    exit 0
else
    echo \"FAIL: Rollback claimed success or did not report failure\"
    exit 1
fi
