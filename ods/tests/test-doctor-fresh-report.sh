#!/usr/bin/env bash
# `ods doctor` shows only the report its own run wrote. It used a fixed /tmp
# path, ignored the doctor's exit status and displayed whatever file was
# there, so after a failed run it showed an old install's model and runtime
# (fleet row 21, Strixy, after a switch to API mode). The doctor's scratch
# files are private to each run for the same reason: fixed names in the
# shared /tmp let another user's earlier run make every later run fail.
if [ "${BASH_VERSINFO[0]:-0}" -lt 4 ]; then
    for candidate in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        [[ -x "$candidate" ]] && exec "$candidate" "$0" "$@"
    done
    echo "FAIL: Bash 4+ is required" >&2
    exit 1
fi
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fixture="$(mktemp -d)"
trap 'rm -rf -- "$fixture"' EXIT
fail() { echo "FAIL: $*" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || fail "python3 is required"

# --- ods-cli: cmd_doctor ----------------------------------------------------
extract() {
    awk -v signature="^$1[(][)]" '
        $0 ~ signature { in_block = 1 }
        in_block { print }
        in_block && /^}$/ { exit }
    ' "$ROOT/ods-cli"
}
body="$(extract cmd_doctor)"
[[ -n "$body" ]] || fail "cmd_doctor was not found in ods-cli"
eval "$body"
eval "$(grep -E '^(warn|error)[(][)] ' "$ROOT/ods-cli")"
RED="" GREEN="" YELLOW="" BLUE="" CYAN="" NC=""
check_install() { :; }

INSTALL_DIR="$fixture/install"
mkdir -p "$INSTALL_DIR/scripts" "$fixture/tmp"
cat > "$INSTALL_DIR/scripts/ods-doctor.sh" <<'EOF'
#!/usr/bin/env bash
if [[ "${FAKE_DOCTOR:-}" == fail ]]; then
    echo "fake doctor: the capability profile could not be written" >&2
    exit 1
fi
cat > "$1" <<'JSON'
{"runtime": {"docker_cli": true, "docker_daemon": true, "compose_cli": true,
  "dashboard_http": true, "webui_http": true,
  "llm_backend": {"status": "ok", "provider": "openai-compatible",
                  "url": "https://api.example.test", "model": "FRESH-MODEL"}},
 "preflight": {"checks": []}, "summary": {}}
JSON
EOF
chmod +x "$INSTALL_DIR/scripts/ods-doctor.sh"
stale='{"runtime": {"llm_backend": {"status": "ok", "provider": "llama-server (Windows)", "model": "STALE-35B.gguf"}}}'

# 1. A failed run never shows the report an earlier run left at the path.
printf '%s\n' "$stale" > "$fixture/report.json"
if output="$( (TMPDIR="$fixture/tmp" FAKE_DOCTOR=fail cmd_doctor --report "$fixture/report.json") 2>&1 )"; then
    fail "a failed doctor run exited 0"
fi
grep -q "STALE-35B" <<<"$output" && fail "a failed run showed the stale report: $output"
grep -qF "fake doctor: the capability profile could not be written" <<<"$output" \
    || fail "the doctor's own output was not shown: $output"
grep -qF "https://discord.gg/4ntNp9MAwC" <<<"$output" || fail "a failed run did not point at help: $output"
echo "PASS: a failed run shows the doctor's output and help, never an earlier report"

# 2. A finished run shows this run's report and saves it at the requested path.
# The exit status reflects the report's findings, which this check ignores.
output="$( (TMPDIR="$fixture/tmp" cmd_doctor --report "$fixture/report.json") 2>&1 )" || true
grep -qF "FRESH-MODEL" <<<"$output" || fail "this run's report was not shown: $output"
grep -qF "FRESH-MODEL" "$fixture/report.json" || fail "this run's report was not saved at the requested path"
echo "PASS: a finished run shows and saves its own report"

# 3. A path this user cannot write keeps the run's own report and says where.
output="$( (TMPDIR="$fixture/tmp" cmd_doctor --report "$fixture/missing/report.json") 2>&1 )" || true
grep -qF "FRESH-MODEL" <<<"$output" || fail "an unwritable report path hid this run's report: $output"
grep -qF "This run's report is at $fixture/tmp/" <<<"$output" || fail "the kept report's location was not named: $output"
echo "PASS: an unwritable report path still shows this run's report and names where it is"

# --- scripts/ods-doctor.sh: private scratch files -----------------------------
grep -nE '/tmp/ods-doctor-(capabilities|preflight)' "$ROOT/scripts/ods-doctor.sh" \
    && fail "the doctor still names a fixed scratch file in the shared /tmp"

tree="$fixture/tree"
mkdir -p "$tree/bin" "$tree/extensions/services" "$fixture/doctor-tmp"
cp -R "$ROOT/lib" "$ROOT/scripts" "$ROOT/config" "$tree/"
cp "$ROOT/.env.schema.json" "$tree/"
for manifest in "$ROOT"/extensions/services/*/manifest.yaml; do
    svc="$(basename "$(dirname "$manifest")")"
    mkdir -p "$tree/extensions/services/$svc"
    cp "$manifest" "$tree/extensions/services/$svc/"
done
printf 'ODS_MODE=local\nGPU_BACKEND=cpu\n' > "$tree/.env"
# No daemon. Each docker call records the scratch directory the run is using.
cat > "$tree/bin/docker" <<'EOF'
#!/bin/sh
find "$TMPDIR" -name '*.json' >> "$SCRATCH_LOG" 2>&1
echo "Cannot connect to the Docker daemon" >&2
exit 1
EOF
chmod +x "$tree/bin/docker"
(
    cd "$tree"
    PATH="$tree/bin:$PATH" TMPDIR="$fixture/doctor-tmp" SCRATCH_LOG="$fixture/scratch.log" \
        "$BASH" scripts/ods-doctor.sh "$fixture/doctor-report.json"
) >"$fixture/doctor.out" 2>&1 || fail "the doctor failed: $(tail -5 "$fixture/doctor.out")"
[[ -s "$fixture/doctor-report.json" ]] || fail "the doctor wrote no report"
grep -qE '/ods-doctor\.[^/]+/capabilities\.json$' "$fixture/scratch.log" \
    || fail "the capability profile was not in a private per-run directory: $(head -3 "$fixture/scratch.log" 2>/dev/null)"
leftover="$(find "$fixture/doctor-tmp" -mindepth 1 -maxdepth 1 -name 'ods-doctor.*')"
[[ -z "$leftover" ]] || fail "the doctor left its scratch directory behind: $leftover"
echo "PASS: the doctor keeps scratch files in a private per-run directory and removes it"
