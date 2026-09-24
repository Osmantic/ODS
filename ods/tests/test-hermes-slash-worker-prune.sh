#!/usr/bin/env bash
# Behavioural tests for scripts/prune-hermes-slash-workers.sh.
#
# The existing contract test only asserts that guardrail strings appear in the
# script. Nothing exercised the part that decides which pids get killed, which
# is where the interesting failure modes live: the script reads a process table
# out of the Hermes container, and different images ship different `ps`
# implementations.
#
# Run: bash tests/test-hermes-slash-worker-prune.sh

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PRUNE="$ROOT_DIR/scripts/prune-hermes-slash-workers.sh"

PASS=0
FAIL=0

pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; echo "       $2"; FAIL=$((FAIL + 1)); }

check_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        pass "$label"
    else
        fail "$label" "expected [$expected] got [$actual]"
    fi
}

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

# ── Process-table fixtures ────────────────────────────────────────────────
# pid 101/103 are old, 102 is young. pid 1 and 200 are not slash workers.

cat > "$WORKDIR/procps.txt" <<'EOF'
    1     9999 /sbin/init
  101     7200 python -m hermes.tui_gateway.slash_worker --id a
  102       60 python -m hermes.tui_gateway.slash_worker --id b
  103     5400 python -m hermes.tui_gateway.slash_worker --id c
  200      120 /bin/sh
EOF

# `ps -eo pid=,args=` — POSIX subset, no elapsed-time column.
cat > "$WORKDIR/posix.txt" <<'EOF'
    1 /sbin/init
  101 python -m hermes.tui_gateway.slash_worker --id a
  102 python -m hermes.tui_gateway.slash_worker --id b
  103 python -m hermes.tui_gateway.slash_worker --id c
  200 /bin/sh
EOF

# `ps -ef` — leads with UID, so the pid sits in column 2. A numeric UID is what
# containers print when the running uid has no passwd entry.
cat > "$WORKDIR/ef.txt" <<'EOF'
UID          PID    PPID  C STIME TTY          TIME CMD
1000           1       0  0 10:00 ?        00:00:00 /sbin/init
1000         101       1  0 08:00 ?        00:00:01 python -m hermes.tui_gateway.slash_worker --id a
1000         102       1  0 11:00 ?        00:00:01 python -m hermes.tui_gateway.slash_worker --id b
1000         103       1  0 09:00 ?        00:00:01 python -m hermes.tui_gateway.slash_worker --id c
EOF

# ── docker shim ───────────────────────────────────────────────────────────
# Models a container image by deciding which `ps` invocations succeed:
#   ODS_TEST_PS_MODE=procps  → `ps -eo pid=,etimes=,args=` works
#   ODS_TEST_PS_MODE=posix   → it fails; only `ps -ef` / `ps -eo pid=,args=` work
# Killed pids are appended to $ODS_TEST_KILL_LOG.

mkdir -p "$WORKDIR/bin"
cat > "$WORKDIR/bin/docker" <<'SHIM'
#!/usr/bin/env bash
set -uo pipefail

case "${1:-}" in
    ps)
        [[ "${ODS_TEST_CONTAINER_MISSING:-0}" == "1" ]] && exit 0
        echo "ods-hermes"
        exit 0
        ;;
    exec)
        shift
        while [[ "${1:-}" == -* ]]; do shift; done   # drop -i and friends
        shift                                        # container name
        shift 2>/dev/null                            # sh
        [[ "${1:-}" == "-c" ]] && shift
        cmd="${1:-}"

        if [[ "${ODS_TEST_PS_MODE:-procps}" == "broken" ]]; then
            exit 1
        fi

        if [[ "$cmd" == *"while read"* ]]; then
            cat >> "$ODS_TEST_KILL_LOG"
            exit 0
        fi

        if [[ "$cmd" == *"etimes"* ]]; then
            if [[ "${ODS_TEST_PS_MODE:-procps}" == "procps" ]]; then
                cat "$ODS_TEST_PROCPS_FIXTURE"
                exit 0
            fi
            # etimes is unsupported here. Honour an inline `|| ps -ef` fallback
            # exactly the way a container shell would.
            if [[ "$cmd" == *"ps -ef"* ]]; then
                cat "$ODS_TEST_EF_FIXTURE"
                exit 0
            fi
            exit 1
        fi

        if [[ "$cmd" == *"pid=,args="* ]]; then
            cat "$ODS_TEST_POSIX_FIXTURE"
            exit 0
        fi

        exit 1
        ;;
esac
exit 0
SHIM
chmod +x "$WORKDIR/bin/docker"

export ODS_TEST_PROCPS_FIXTURE="$WORKDIR/procps.txt"
export ODS_TEST_POSIX_FIXTURE="$WORKDIR/posix.txt"
export ODS_TEST_EF_FIXTURE="$WORKDIR/ef.txt"

run_prune() {
    # Usage: run_prune <ps-mode> <output-var-file> [extra args...]
    local mode="$1"; shift
    local outfile="$1"; shift
    ODS_TEST_PS_MODE="$mode" \
    ODS_TEST_KILL_LOG="$KILL_LOG" \
    PATH="$WORKDIR/bin:$PATH" \
        bash "$PRUNE" "$@" > "$outfile" 2>&1
    echo $?
}

selected_pids() {
    # pids the script reported as selected for pruning, comma separated
    grep -oE '^  pid=[0-9]+' "$1" | grep -oE '[0-9]+' | paste -sd, -
}

killed_pids() {
    if [[ -s "$KILL_LOG" ]]; then
        tr -d ' ' < "$KILL_LOG" | grep -E '^[0-9]+$' | paste -sd, -
    else
        echo ""
    fi
}

# ── 1. Fixture path: age policy ───────────────────────────────────────────

OUT="$WORKDIR/out1.txt"
KILL_LOG="$WORKDIR/kill1.txt"; : > "$KILL_LOG"
ODS_HERMES_SLASH_WORKER_PS_FIXTURE="$WORKDIR/procps.txt" \
    bash "$PRUNE" --max-age-seconds 3600 --max-count 99 > "$OUT" 2>&1
check_eq "fixture: only workers older than max-age are selected" "101,103" "$(selected_pids "$OUT")"

# ── 2. Fixture path: count policy prunes the oldest overage ───────────────

OUT="$WORKDIR/out2.txt"
ODS_HERMES_SLASH_WORKER_PS_FIXTURE="$WORKDIR/procps.txt" \
    bash "$PRUNE" --max-age-seconds 99999 --max-count 2 > "$OUT" 2>&1
check_eq "fixture: count overage prunes the single oldest worker" "101" "$(selected_pids "$OUT")"

# ── 3. Container path, procps layout ──────────────────────────────────────

OUT="$WORKDIR/out3.txt"
KILL_LOG="$WORKDIR/kill3.txt"; : > "$KILL_LOG"
rc="$(run_prune procps "$OUT" --max-age-seconds 3600 --max-count 99 --force)"
check_eq "procps container: exits 0" "0" "$rc"
check_eq "procps container: selects the aged workers" "101,103" "$(selected_pids "$OUT")"
check_eq "procps container: kills exactly those pids" "101,103" "$(killed_pids)"

# ── 4. Container path, image without an elapsed-time column ───────────────
#
# This is the regression. The script used to ask the container shell for
# `ps -eo pid=,etimes=,args= || ps -ef`; on the fallback branch column 1 is the
# UID, so pid 1000 (the uid) was pruned and the real workers were left running.

OUT="$WORKDIR/out4.txt"
KILL_LOG="$WORKDIR/kill4.txt"; : > "$KILL_LOG"
rc="$(run_prune posix "$OUT" --max-age-seconds 99999 --max-count 1 --force)"
check_eq "posix container: exits 0" "0" "$rc"
check_eq "posix container: selects real worker pids, never the uid column" "101,102" "$(selected_pids "$OUT")"
check_eq "posix container: kills real worker pids" "101,102" "$(killed_pids)"

if grep -q 'pid=1000' "$OUT"; then
    fail "posix container: uid column must not be read as a pid" "output selected pid=1000"
else
    pass "posix container: uid column must not be read as a pid"
fi

# ── 5. Unknown ages never trip the age policy on their own ────────────────

OUT="$WORKDIR/out5.txt"
KILL_LOG="$WORKDIR/kill5.txt"; : > "$KILL_LOG"
rc="$(run_prune posix "$OUT" --max-age-seconds 0 --max-count 99)"
check_eq "posix container: unknown age is not treated as older than max-age" "" "$(selected_pids "$OUT")"

# ── 6. Stopped container does not leak its notice into the worker stream ──

OUT="$WORKDIR/out6.txt"
KILL_LOG="$WORKDIR/kill6.txt"; : > "$KILL_LOG"
rc="$(ODS_TEST_CONTAINER_MISSING=1 run_prune procps "$OUT")"
check_eq "stopped container: exits 0" "0" "$rc"
if grep -q "is not running" "$OUT"; then
    pass "stopped container: reports that the container is not running"
else
    fail "stopped container: reports that the container is not running" "$(cat "$OUT")"
fi

OUT="$WORKDIR/out7.txt"
KILL_LOG="$WORKDIR/kill7.txt"; : > "$KILL_LOG"
rc="$(run_prune broken "$OUT")"
check_eq "unreadable process table: exits nonzero" "1" "$rc"
if grep -q "could not read the process table" "$OUT"; then
    pass "unreadable process table: reports the inspection failure"
else
    fail "unreadable process table: reports the inspection failure" "$(cat "$OUT")"
fi
if grep -q "no Hermes slash workers found" "$OUT"; then
    fail "unreadable process table: never claims that no workers exist" "$(cat "$OUT")"
else
    pass "unreadable process table: never claims that no workers exist"
fi

# 8. JSON mode exposes the selected workers and policy at the public boundary.
OUT="$WORKDIR/out8.json"
ODS_HERMES_SLASH_WORKER_PS_FIXTURE="$WORKDIR/procps.txt" \
    bash "$PRUNE" --json --max-age-seconds 3600 --max-count 99 > "$OUT" 2>&1
JSON_SUMMARY="$(python3 - "$OUT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    report = json.load(handle)
print(
    report["outcome"],
    report["dry_run"],
    report["worker_count"],
    report["candidate_count"],
    report["policy"]["max_count"],
    report["policy"]["max_age_seconds"],
    ",".join(str(item["pid"]) for item in report["candidates"]),
)
PY
)"
check_eq "json: reports policy and selected workers" \
    "candidates-selected True 3 2 99 3600 101,103" "$JSON_SUMMARY"

OUT="$WORKDIR/out9.json"
ODS_HERMES_SLASH_WORKER_PS_FIXTURE="$WORKDIR/procps.txt" \
    bash "$PRUNE" --json --force --max-age-seconds 3600 --max-count 99 > "$OUT" 2>&1
JSON_OUTCOME="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["outcome"])' "$OUT")"
check_eq "json: fixture force remains read-only" "fixture-read-only" "$JSON_OUTCOME"

# ── Summary ───────────────────────────────────────────────────────────────

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] || exit 1
echo "[PASS] Hermes slash worker prune behaviour"
