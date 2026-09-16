#!/usr/bin/env bash
# scripts/simulate-installers.sh feeds each run's exit code into summary.json,
# SUMMARY.md and the golden-path evidence that `make gate` and CI publish. It
# captured them with `if ! cmd; then X=$?; fi`, where $? is the negated
# result of the `!` pipeline and therefore always 0, so every failed dry-run
# was recorded as exit code 0. Run the harness against a stub tree whose
# installers fail with distinct codes and check the codes it records.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HARNESS="${ODS_SIMULATE_UNDER_TEST:-$ROOT_DIR/scripts/simulate-installers.sh}"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

[[ -f "$HARNESS" ]] || fail "missing $HARNESS"
command -v jq >/dev/null 2>&1 || fail "jq is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required by the harness"

# Stub tree: the harness resolves everything relative to its own parent
# directory and runs the installers from there.
TREE="$TMP_DIR/tree"
mkdir -p "$TREE/scripts" "$TREE/installers" "$TREE/lib" "$TREE/config"
cp "$HARNESS" "$TREE/scripts/simulate-installers.sh"
cp "$ROOT_DIR/lib/python-cmd.sh" "$TREE/lib/"
cp "$ROOT_DIR/config/golden-paths.json" "$TREE/config/"

cat > "$TREE/install-core.sh" <<'EOF'
#!/usr/bin/env bash
echo "stub linux dry-run: failing on purpose"
exit 3
EOF
cat > "$TREE/installers/macos.sh" <<'EOF'
#!/usr/bin/env bash
report=""; doctor=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --report) report="$2"; shift 2 ;;
        --doctor-report) doctor="$2"; shift 2 ;;
        *) shift ;;
    esac
done
printf '{"summary":{"blockers":0,"warnings":0}}\n' > "$report"
printf '{"summary":{}}\n' > "$doctor"
echo "stub macos installer: failing on purpose"
exit 4
EOF
cat > "$TREE/scripts/preflight-engine.sh" <<'EOF'
#!/usr/bin/env bash
report=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --report) report="$2"; shift 2 ;;
        *) shift ;;
    esac
done
printf '{"summary":{"blockers":0,"warnings":0}}\n' > "$report"
EOF
cat > "$TREE/scripts/ods-doctor.sh" <<'EOF'
#!/usr/bin/env bash
printf '{"summary":{"runtime_ready":false}}\n' > "$1"
exit 5
EOF
chmod +x "$TREE"/install-core.sh "$TREE"/installers/macos.sh "$TREE"/scripts/*.sh

OUT="$TMP_DIR/out"
if ! (cd "$TREE" && bash scripts/simulate-installers.sh "$OUT") >"$TMP_DIR/harness.log" 2>&1; then
    fail "harness did not complete against the stub tree: $(tail -n 3 "$TMP_DIR/harness.log")"
fi
[[ -f "$OUT/summary.json" ]] || fail "harness wrote no summary.json"

linux_exit="$(jq -r '.runs.linux_dryrun.exit_code' "$OUT/summary.json")"
macos_exit="$(jq -r '.runs.macos_installer_mvp.exit_code' "$OUT/summary.json")"
doctor_exit="$(jq -r '.runs.doctor_snapshot.exit_code' "$OUT/summary.json")"
[[ "$linux_exit" == "3" ]] || fail "summary.json records linux_dryrun exit_code=$linux_exit, the stub exited 3"
[[ "$macos_exit" == "4" ]] || fail "summary.json records macos_installer_mvp exit_code=$macos_exit, the stub exited 4"
[[ "$doctor_exit" == "5" ]] || fail "summary.json records doctor_snapshot exit_code=$doctor_exit, the stub exited 5"
pass "summary.json records the real exit code of each failed run"

grep -qx -- '- Exit code: 3' "$OUT/SUMMARY.md" || fail "SUMMARY.md does not show the Linux dry-run exit code 3"
grep -qx -- '- Exit code: 4' "$OUT/SUMMARY.md" || fail "SUMMARY.md does not show the macOS installer exit code 4"
pass "SUMMARY.md shows the real exit codes"

# A failed macOS run with a clean preflight must not count as a passing
# golden path; it did when the exit code was recorded as 0.
apple_status="$(jq -r '.scenarios[] | select(.simulation_run == "macos_installer_mvp") | .status' "$OUT/golden-paths.json" | head -n 1)"
[[ "$apple_status" == "fail" ]] || fail "golden path backed by the failed macOS run is '$apple_status', expected fail"
pass "golden-path evidence fails when its simulation run failed"
