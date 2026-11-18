#!/usr/bin/env bash
set -euo pipefail
umask 077
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/installers/lib/pixel-host-install.sh"
TEST_ROOT="$(mktemp -d)"
trap 'rm -rf -- "$TEST_ROOT"' EXIT
export PROBE_COUNTER="$TEST_ROOT/count"
program="$TEST_ROOT/probe.py"
cat > "$program" <<'PY'
import json, os, pathlib, sys
p=pathlib.Path(os.environ['PROBE_COUNTER'])
count=int(p.read_text() if p.exists() else '0')+1
p.write_text(str(count))
case=os.environ['PROBE_CASE']; stage=os.environ['PROBE_STAGE']
if case=='request' or (case=='transient' and count==1):
    print('PRIVATE_PROBE_DETAIL_MUST_NOT_APPEAR', file=sys.stderr)
    raise SystemExit(7)
if case=='malformed':
    print('PRIVATE_PROBE_DETAIL_MUST_NOT_APPEAR {')
    raise SystemExit()
if stage=='extension-manager':
    result={'schemaVersion':1,'kind':'ods-pixel-extension-lifecycle','action':'inspect','extensionId':'crewai',
            'outcome':'inspected','changed':False,'externalEffectOccurred':False,
            'requiredConfiguration':[],'optionalConfiguration':[],'missingConfiguration':[],
            'rollback':{'attempted':False,'succeeded':None},
            'boundary':'Scoped ODS extension lifecycle proxy; it grants no Docker, shell, credential, arbitrary HTTP, or data-purge authority.'}
elif stage=='artifact-promoter':
    result={'schemaVersion':1,'kind':'ods-pixel-download-promotion','status':'ok',
            'boundary':'Verified create-only promotion from Pixel Operations quarantine into the configured owner workspace; no arbitrary source, overwrite, execution, or path traversal authority.'}
else:
    result={'schemaVersion':1,'kind':'ods-pixel-workspace-preview','status':'ok','port':9437,
            'boundary':'Create-only static-site snapshot from the configured Pixel workspace to a dedicated loopback preview origin; no arbitrary host path, network destination, server process, overwrite, or execution authority.'}
if case=='mismatch': result['kind']='wrong'; result['privateDetail']='PRIVATE_PROBE_DETAIL_MUST_NOT_APPEAR'
print(json.dumps(result))
PY
ods_sudo() { [[ "$1" == -u && "$2" == pixel-ops-broker ]]; shift 2; "$@"; }
ods_pixel_run_as_owner() { shift 2; "$@"; }
run_probe() {
    case "$PROBE_STAGE" in
        extension-manager) _ods_pixel_wait_extension_manager_probe "$program" crewai "$1" 0 ;;
        artifact-promoter) _ods_pixel_wait_artifact_promoter_probe owner "$TEST_ROOT" "$program" "$1" 0 ;;
        workspace-preview) _ods_pixel_wait_workspace_preview_probe owner "$TEST_ROOT" "$program" 9437 "$1" 0 ;;
    esac
}
for PROBE_STAGE in extension-manager artifact-promoter workspace-preview; do
    export PROBE_STAGE
    for PROBE_CASE in request mismatch malformed; do
        export PROBE_CASE
        rm -f "$PROBE_COUNTER"
        if run_probe 2 >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"; then
            printf 'FAIL: %s accepted %s\n' "$PROBE_STAGE" "$PROBE_CASE" >&2
            exit 1
        fi
        [[ "$(cat "$PROBE_COUNTER")" == 2 ]]
        [[ ! -s "$TEST_ROOT/stdout" && "$(wc -l < "$TEST_ROOT/stderr")" == 1 ]]
        grep -F "Pixel $PROBE_STAGE readiness failed after 2 attempt(s):" "$TEST_ROOT/stderr" >/dev/null
        if [[ "$PROBE_CASE" == request ]]; then
            grep -F 'last probe command exited 7.' "$TEST_ROOT/stderr" >/dev/null
        else
            grep -F 'last probe receipt did not match the required contract.' "$TEST_ROOT/stderr" >/dev/null
        fi
        ! grep -F 'PRIVATE_PROBE_DETAIL_MUST_NOT_APPEAR' "$TEST_ROOT/stderr" >/dev/null
    done
    # This is intentionally a direct invocation with errexit enabled: a
    # failed first request must not abort the established retry behavior.
    export PROBE_CASE=transient
    rm -f "$PROBE_COUNTER"
    run_probe 3 >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"
    [[ "$(cat "$PROBE_COUNTER")" == 2 && ! -s "$TEST_ROOT/stdout" && ! -s "$TEST_ROOT/stderr" ]]
    export PROBE_CASE=success
    rm -f "$PROBE_COUNTER"
    run_probe 1 >"$TEST_ROOT/stdout" 2>"$TEST_ROOT/stderr"
    [[ "$(cat "$PROBE_COUNTER")" == 1 && ! -s "$TEST_ROOT/stdout" && ! -s "$TEST_ROOT/stderr" ]]
done
printf '%s\n' 'PASS: all three readiness probes preserve retries, strict receipts and private output while identifying terminal failures'
