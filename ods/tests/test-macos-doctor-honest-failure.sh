#!/usr/bin/env bash
# Regression (issue #3786): installers/macos.sh ran the doctor as
#   ods-doctor.sh "$DOCTOR_FILE" >/dev/null 2>&1 || true
#   echo "[INFO] Doctor report: $DOCTOR_FILE"
# On stock macOS Bash 3.2 the doctor exits before writing anything, the
# wrapper swallowed that, and then announced a report file that did not
# exist. The block must now surface a WARN plus the doctor's own stderr,
# and only print the INFO line when the report actually exists.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="$ROOT_DIR/installers/macos.sh"

fail() { echo "[FAIL] $*" >&2; exit 1; }
pass() { echo "[PASS] $*"; }

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Extract the doctor-invocation block (the `if [[ -x ...ods-doctor.sh ]]`
# through its closing `fi`) and run it against a fake SCRIPT_DIR.
BLOCK="$(awk '/if \[\[ -x "\$SCRIPT_DIR\/scripts\/ods-doctor\.sh" \]\]; then/,/^fi$/' "$TARGET")"
[[ -n "$BLOCK" ]] || fail "could not extract doctor block from macos.sh"

run_block() {
    # Args: <fake_script_dir> <doctor_file>
    bash -c '
        set -euo pipefail
        SCRIPT_DIR="$1"
        DOCTOR_FILE="$2"
        eval "$3"
    ' _ "$1" "$2" "$BLOCK"
}

# ── Case 1: doctor fails (Bash 3.2 style) → WARN + stderr, no INFO ────────
fake_dir="$tmp/fake1"
mkdir -p "$fake_dir/scripts"
cat > "$fake_dir/scripts/ods-doctor.sh" <<'EOF'
#!/usr/bin/env bash
echo "ERROR: service-registry.sh requires Bash 4.0+ (current: 3.2.57(1)-release)" >&2
exit 1
EOF
chmod +x "$fake_dir/scripts/ods-doctor.sh"

out="$(run_block "$fake_dir" "$tmp/doc1.json")"
grep -q '\[WARN\] Doctor report unavailable' <<<"$out" \
    || fail "failing doctor did not produce WARN; got: $out"
grep -q 'Bash 4.0' <<<"$out" \
    || fail "doctor stderr was not surfaced; got: $out"
grep -q '\[INFO\] Doctor report' <<<"$out" \
    && fail "INFO announced for a report that was never written"
pass "doctor failure surfaces WARN with the doctor's stderr, no phantom INFO"

# ── Case 2: doctor succeeds → INFO only when report exists ────────────────
fake_dir="$tmp/fake2"
mkdir -p "$fake_dir/scripts"
cat > "$fake_dir/scripts/ods-doctor.sh" <<'EOF'
#!/usr/bin/env bash
printf '{"ok":true}\n' > "$1"
EOF
chmod +x "$fake_dir/scripts/ods-doctor.sh"

out="$(run_block "$fake_dir" "$tmp/doc2.json")"
grep -q "\[INFO\] Doctor report: $tmp/doc2.json" <<<"$out" \
    || fail "successful doctor did not announce report; got: $out"
grep -q '\[WARN\]' <<<"$out" && fail "WARN printed despite successful doctor run"
pass "successful doctor announces the report path"

# ── Case 3: doctor exits 0 but writes nothing → still WARN ────────────────
fake_dir="$tmp/fake3"
mkdir -p "$fake_dir/scripts"
cat > "$fake_dir/scripts/ods-doctor.sh" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "$fake_dir/scripts/ods-doctor.sh"

out="$(run_block "$fake_dir" "$tmp/doc3.json")"
grep -q '\[WARN\] Doctor report unavailable' <<<"$out" \
    || fail "silent no-write doctor still produced INFO; got: $out"
pass "doctor that writes no report is not announced as INFO"

echo "All macOS doctor honest-failure checks passed."
