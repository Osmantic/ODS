#!/usr/bin/env bash
# ============================================================================
# _compose_run_with_summary failure evidence must only name containers that
# belong to the failing command. Compose's "Found orphan containers" warning
# names stale, unrelated containers (e.g. an exited ods-n8n); their logs must
# not be printed as evidence for a targeted service failure.
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLI="$SCRIPT_DIR/../ods-cli"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/bin"
cat > "$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
# Stub: exited containers are ods-n8n (orphan) and ods-searxng (target).
args="$*"
case "$args" in
    "compose "*" ps -a --format {{.Name}} searxng") echo "ods-searxng" ;;
    "compose "*" ps -a --format {{.Name}}"*) printf 'ods-n8n\nods-searxng\nods-llama-server\n' ;;
    "compose "*" up "*)
        echo "time=x level=warning msg=\"Found orphan containers ([ods-n8n]) for this project.\""
        echo "Error response from daemon: No such image: searxng/searxng:latest (ods-searxng failed)"
        exit 1 ;;
    "inspect "*)
        case "${args##* }" in
            ods-n8n|ods-searxng) echo "exited none" ;;
            *) echo "running healthy" ;;
        esac ;;
    "logs "*) echo "LOGS-OF ${args##* }" ;;
esac
exit 0
STUB
chmod +x "$TMP/bin/docker"
export PATH="$TMP/bin:$PATH" TMPDIR="$TMP"
unset DOCKER_STUB_MODE

log() { echo "[ods] $1"; }
success() { echo "ok $1"; }
warn() { echo "warn $1"; }
log_error() { echo "err $1"; }
_check_docker_access() { :; }
eval "$(awk '/^_compose_run_with_summary\(\) \{/,/^\}$/' "$CLI")"

fail=0
check() { # name, output, pattern, expect(present|absent)
    if grep -qF -- "$3" <<<"$2"; then found=present; else found=absent; fi
    if [[ "$found" == "$4" ]]; then echo "PASS: $1"; else echo "FAIL: $1"; fail=1; fi
}

rc=0
out=$(_compose_run_with_summary "Restarting searxng" -f base.yml up -d --force-recreate --no-build --pull never searxng 2>&1) || rc=$?
[[ "$rc" -eq 1 ]] && echo "PASS: targeted rc propagated" || { echo "FAIL: rc=$rc"; fail=1; }
check "targeted: orphan ods-n8n not reported" "$out" "container ods-n8n (evidence)" absent
check "targeted: failed ods-searxng logs shown" "$out" "LOGS-OF ods-searxng" present

rc=0
out=$(_compose_run_with_summary "Restarting all services" -f base.yml up -d --force-recreate 2>&1) || rc=$?
check "full-stack: orphan ods-n8n not reported" "$out" "container ods-n8n (evidence)" absent
check "full-stack: failed ods-searxng logs shown" "$out" "LOGS-OF ods-searxng" present

exit "$fail"
