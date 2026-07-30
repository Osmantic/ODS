#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/07-devtools.sh"
SERVICE="$ROOT_DIR/opencode/opencode-web.service"

pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; exit 1; }

echo "=== Linux OpenCode path tests ==="

grep -q 'type -P opencode' "$PHASE" \
  && pass "phase resolves executable OpenCode from PATH" \
  || fail "phase must resolve executable OpenCode from PATH without accepting shell functions"

grep -q '_opencode_candidate_is_file' "$PHASE" \
  && pass "phase validates OpenCode as an absolute executable file" \
  || fail "phase must validate OpenCode as an absolute executable file"

grep -q 'OPENCODE_BIN="\$(_find_opencode_bin || true)"' "$PHASE" \
  && pass "phase stores resolved OpenCode binary" \
  || fail "phase must store resolved OpenCode binary"

grep -q '\[\[ -n "\$OPENCODE_BIN" && -x "\$OPENCODE_BIN" \]\]' "$PHASE" \
  && pass "phase configures PATH-installed OpenCode" \
  || fail "phase must not require ~/.opencode/bin/opencode for configuration"

grep -q '__OPENCODE_BIN__' "$SERVICE" \
  && pass "systemd service templates resolved binary" \
  || fail "systemd service must not hard-code ~/.opencode/bin/opencode"

grep -q '__OPENCODE_BIN_DIR__' "$SERVICE" \
  && pass "systemd service templates resolved binary directory" \
  || fail "systemd service PATH must include resolved binary directory"

if grep -q 'ExecStart=__HOME__/.opencode/bin/opencode' "$SERVICE"; then
  fail "systemd service still hard-codes ~/.opencode/bin/opencode"
fi


echo ""
echo "--- OPENCODE_PORT override ---"

grep -q '__OPENCODE_PORT__' "$SERVICE" \
  && pass "systemd service templates the listen port" \
  || fail "systemd service must template the listen port, not hard-code it"

if grep -qE 'serve --port [0-9]+' "$SERVICE"; then
  fail "systemd service still hard-codes a numeric --port"
fi
pass "systemd service has no hard-coded --port literal"

grep -q '__OPENCODE_PORT__' "$PHASE" \
  && pass "phase substitutes the port placeholder" \
  || fail "phase must substitute __OPENCODE_PORT__"

# Behavioural: run the phase's own resolution + rendering against fixtures.
render_unit() {
  # $1 = OPENCODE_PORT line to write into .env ("" writes no key)
  local env_line="$1"
  local work
  work="$(mktemp -d)"
  mkdir -p "$work/install/opencode"
  cp "$SERVICE" "$work/install/opencode/opencode-web.service"
  : > "$work/install/.env"
  [[ -n "$env_line" ]] && printf '%s\n' "$env_line" >> "$work/install/.env"
  mkdir -p "$work/systemd"

  awk '/^            # OPENCODE_PORT is the documented override/ {inside=1}
       inside {print}
       inside && /rm -f "\$svc_tmp"/ {rendered=1}
       inside && rendered && /^            fi$/ {exit}' "$PHASE" > "$work/block.sh"
  [[ -s "$work/block.sh" ]] || fail "could not extract the port-resolution block from the phase"

  (
    set -euo pipefail
    INSTALL_DIR="$work/install"
    SYSTEMD_USER_DIR="$work/systemd"
    OPENCODE_BIN="/usr/local/bin/opencode"
    HOME="$work/home"
    ai_warn() { printf 'WARN %s\n' "$*" >&2; }
    _sed_i() {
      local expr="$1" file="$2"
      sed "$expr" "$file" > "$file.rendered" && mv "$file.rendered" "$file"
    }
    # shellcheck disable=SC1090
    . "$work/block.sh"
  ) 2>/dev/null

  cat "$work/systemd/opencode-web.service" 2>/dev/null
  rm -rf "$work"
}

rendered="$(render_unit 'OPENCODE_PORT=3103')"
grep -q -- '--port 3103' <<<"$rendered" \
  && pass "operator OPENCODE_PORT reaches the rendered unit" \
  || fail "rendered unit ignored OPENCODE_PORT=3103: $(grep ExecStart <<<"$rendered")"

rendered="$(render_unit '')"
grep -q -- '--port 3003' <<<"$rendered" \
  && pass "missing OPENCODE_PORT falls back to 3003" \
  || fail "rendered unit did not fall back to 3003: $(grep ExecStart <<<"$rendered")"

rendered="$(render_unit 'OPENCODE_PORT=not-a-port')"
grep -q -- '--port 3003' <<<"$rendered" \
  && pass "invalid OPENCODE_PORT falls back to 3003" \
  || fail "rendered unit accepted a non-numeric port: $(grep ExecStart <<<"$rendered")"

rendered="$(render_unit '')"
if grep -q '__OPENCODE_PORT__' <<<"$rendered"; then
  fail "rendered unit still contains an unsubstituted placeholder"
fi
pass "rendered unit has no unsubstituted placeholders"

echo "[PASS] Linux OpenCode path tests"
