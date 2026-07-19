#!/usr/bin/env bash
# Regression: Windows `ods restart` must recreate containers so model/env
# changes made after bootstrap hot-swap are visible to env-backed consumers
# such as Perplexica.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_PS1="$ROOT_DIR/installers/windows/ods.ps1"

PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

restart_block="$(awk '
    /function Invoke-Restart/ { in_block=1 }
    in_block { print }
    in_block && /function Invoke-Logs/ { exit }
' "$ODS_PS1")"

[[ -n "$restart_block" ]] \
    && pass "Invoke-Restart block extracted" \
    || fail "Invoke-Restart block missing"

if grep -qF -- '-ComposeArgs @("up", "-d", "--force-recreate", "--no-build", "--pull", "never", $Service)' <<<"$restart_block"; then
    pass "single-service restart recreates the selected container with current .env"
else
    fail "single-service restart must use guarded docker compose up -d --force-recreate <service>"
fi

if grep -qF -- '$restartTargets = Get-ODSRunningComposeServices -ComposeFlags $flags' <<<"$restart_block" &&
   grep -qF -- '$composeArgs = @("up", "-d", "--force-recreate", "--no-build", "--pull", "never")' <<<"$restart_block" &&
   grep -qF -- '$composeArgs += $restartTargets' <<<"$restart_block" &&
   grep -qF -- '-ComposeArgs $composeArgs' <<<"$restart_block"; then
    pass "all-service restart recreates the running compose services with current .env"
else
    fail "all-service restart must target running compose services with guarded docker compose up -d --force-recreate"
fi

if grep -qF -- 'function Get-ODSRunningComposeServices' "$ODS_PS1" &&
   grep -qF -- 'ps --services --filter "status=running"' "$ODS_PS1" &&
   grep -qF -- 'label=com.docker.compose.project=ods' "$ODS_PS1"; then
    pass "all-service restart discovers running services before force-recreate"
else
    fail "all-service restart must discover running services and avoid disabled compose services"
fi

if grep -qF -- 'docker compose force-recreate returned' <<<"$restart_block" &&
   grep -qF -- '$retryArgs = @("up", "-d", "--no-build", "--pull", "never") + $restartTargets' <<<"$restart_block" &&
   grep -qF -- 'Invoke-ODSComposeUpWithStartupRetry -ComposeFlags $flags' <<<"$restart_block"; then
    pass "all-service restart retries start after partial force-recreate failure"
else
    fail "all-service restart must recover from partial force-recreate failures with bounded startup retries"
fi

if grep -qF -- 'function Test-ODSComposeServicesStarted' "$ODS_PS1" &&
   grep -qF -- 'function Invoke-ODSComposeUpWithStartupRetry' "$ODS_PS1" &&
   grep -qF -- 'ODS_RESTART_STARTUP_RETRY_ATTEMPTS' "$ODS_PS1" &&
   grep -qF -- 'ODS_RESTART_STARTUP_RETRY_DELAY_SECONDS' "$ODS_PS1" &&
   grep -qF -- 'targeted services are running or completed cleanly; continuing' "$ODS_PS1"; then
    pass "restart retry validates targeted services before tolerating compose nonzero"
else
    fail "restart retry must be bounded and validate targeted service state before tolerating compose nonzero"
fi

if grep -qF -- '-ComposeArgs @("restart"' <<<"$restart_block"; then
    fail "Invoke-Restart must not use docker compose restart because it preserves stale container env"
else
    pass "Invoke-Restart avoids docker compose restart stale-env behavior"
fi

if grep -qF 'docker compose up --force-recreate failed' <<<"$restart_block"; then
    pass "restart failure message names the recreate command"
else
    fail "restart failure message should name docker compose up --force-recreate"
fi

compose_helper="$(awk '
    /function Initialize-ODSComposeDockerClientConfig/ { in_block=1 }
    in_block { print }
    in_block && /function Get-ODSComposeEnvValue/ { exit }
' "$ROOT_DIR/installers/windows/lib/compose-diagnostics.ps1")"

if grep -qF 'docker-client-public' <<<"$compose_helper" &&
   grep -qF '& docker @dockerClientArgs compose @ComposeFlags @ComposeArgs' <<<"$compose_helper"; then
    pass "Windows CLI compose helper uses install-scoped Docker client config"
else
    fail "Windows CLI compose helper must avoid Docker Desktop credential-helper state"
fi

echo
echo "Windows restart recreate contract: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
