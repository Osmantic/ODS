#!/usr/bin/env bash
# A Linux installer rerun regenerates .env from phase 06's template and keeps
# the owner's existing values. Those values must come back literally: a value
# the dashboard or the owner quoted (a password with '$' or ' #') must not be
# interpolated, truncated or rejected by Docker Compose after the rerun.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT_DIR/installers/phases/06-directories.sh"
DASHBOARD_API="$ROOT_DIR/extensions/services/dashboard-api"
# shellcheck source=../lib/safe-env.sh
. "$ROOT_DIR/lib/safe-env.sh"
# shellcheck source=../lib/dotenv-quote.sh
. "$ROOT_DIR/lib/dotenv-quote.sh"

PASS=0
FAIL=0
pass() { PASS=$((PASS + 1)); printf '[PASS] %s\n' "$1"; }
fail() { FAIL=$((FAIL + 1)); printf '[FAIL] %s\n' "$1" >&2; }

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

# Every template line whose value phase 06 reads back from the existing .env
# must be serialized; a bare ${VALUE} would be re-interpreted by Compose.
if python3 - "$PHASE" <<'PY'
import re
import sys

lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
readers = r"(?:_env_get(?:_preserve_empty|_explicit_first)?|_phase06_env_hex_secret)"
preserved = {
    match.group(1)
    for line in lines
    if (match := re.match(r'\s*(?:local\s+)?([A-Z_][A-Z0-9_]*)="?\$\(' + readers + r"\s", line))
}
start = next(i for i, line in enumerate(lines) if 'cat > "$INSTALL_DIR/.env" << ENV_EOF' in line)
end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "ENV_EOF")
# Validated as empty or a positive integer before the template is rendered.
plain_by_validation = {"PIXEL_INGRESS_GID"}
checked, bare = 0, []
for number in range(start + 1, end):
    match = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", lines[number])
    if not match or match.group(1) in plain_by_validation:
        continue
    key, value = match.groups()
    names = set(re.findall(r"\$\{?([A-Z_][A-Z0-9_]*)", value))
    if not names & preserved:
        continue
    checked += 1
    for name in names & preserved:
        if not re.search(r'dotenv_(?:value|quote) "\$\{?' + name + r"(?::-)?\}?\"", value):
            bare.append(f"line {number + 1}: {lines[number]}")
if checked < 50:
    sys.exit(f"only {checked} preserved template lines found; the guard no longer matches phase 06")
if bare:
    sys.exit("preserved values written without dotenv serialization:\n" + "\n".join(bare))
print(f"{checked} preserved template lines are serialized")
PY
then
    pass "phase 06 serializes every value it preserves from the existing .env"
else
    fail "phase 06 writes a preserved value without dotenv serialization"
fi

# Phase 06 declares its readers inside the generate-env block. Evaluate the
# exact shipped definitions rather than a copy of them.
extract_nested_function() {
    awk -v name="$1" '
        $0 == "    " name "() {" { emit = 1 }
        emit { print }
        emit && $0 == "    }" { exit }
    ' "$PHASE"
}
for reader in _env_get _env_get_preserve_empty; do
    definition="$(extract_nested_function "$reader")"
    if [[ -z "$definition" ]]; then
        fail "could not extract $reader from phase 06"
        exit 1
    fi
    eval "$definition"
done

# Owner values that need .env quoting, serialized by the real dashboard writer.
keys=(N8N_PASS OPENCODE_SERVER_PASSWORD LANGFUSE_INIT_USER_PASSWORD ODS_DEVICE_NAME OPENAI_API_KEY RAG_OPENAI_API_KEY)
literals=(
    'Xk9${pQ#r2Lm'
    "it's\$HOME"
    'my$ecret#1 now'
    'Lab #2'
    ' padded key '
    'back\slash"dq'"'"'sq'
)
if ! python3 - "$DASHBOARD_API" "$tmp_dir/existing.env" "${keys[@]}" -- "${literals[@]}" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])
from env_values import quote_env_value

args = sys.argv[3:]
split = args.index("--")
keys, literals = args[:split], args[split + 1:]
with open(sys.argv[2], "w", encoding="utf-8") as handle:
    handle.write("# written by the dashboard Settings serializer\n")
    for key, literal in zip(keys, literals):
        handle.write(f"{key}={quote_env_value(literal)}\n")
    handle.write("BIND_ADDRESS=0.0.0.0 # LAN access\n")
PY
then
    fail "could not serialize the fixture with the dashboard writer"
    exit 1
fi

_env_existing="$tmp_dir/existing.env"
for index in "${!keys[@]}"; do
    key="${keys[$index]}"
    literal="${literals[$index]}"
    [[ "$(_env_get "$key" "default-must-not-win")" == "$literal" ]] \
        && pass "_env_get reads dashboard-quoted $key literally" \
        || fail "_env_get changed dashboard-quoted $key: $(_env_get "$key" "")"
    [[ "$(_env_get_preserve_empty "$key" "default-must-not-win")" == "$literal" ]] \
        && pass "_env_get_preserve_empty reads dashboard-quoted $key literally" \
        || fail "_env_get_preserve_empty changed dashboard-quoted $key"
done
[[ "$(_env_get BIND_ADDRESS "127.0.0.1")" == "0.0.0.0" ]] \
    && pass "_env_get applies the inline-comment rule before installer logic compares values" \
    || fail "_env_get kept an inline comment in BIND_ADDRESS: $(_env_get BIND_ADDRESS "")"

# Simulate the rerun: read each value with phase 06's reader and write it back
# the way the template now does.
: > "$tmp_dir/rerun.env"
for key in "${keys[@]}"; do
    printf '%s=%s\n' "$key" "$(dotenv_value "$(_env_get "$key" "")")" >> "$tmp_dir/rerun.env"
done
for index in "${!keys[@]}"; do
    key="${keys[$index]}"
    unset "$key"
done
load_env_file "$tmp_dir/rerun.env"
for index in "${!keys[@]}"; do
    key="${keys[$index]}"
    [[ "${!key-}" == "${literals[$index]}" ]] \
        && pass "rerun output keeps $key literal for lib/safe-env.sh" \
        || fail "rerun output changed $key for lib/safe-env.sh: ${!key-}"
done
for key in "${keys[@]}"; do
    [[ "$(grep -m1 "^${key}=" "$tmp_dir/rerun.env")" == "$(grep -m1 "^${key}=" "$tmp_dir/existing.env")" ]] \
        && pass "rerun writes the dashboard-saved $key line back byte-for-byte" \
        || fail "rerun rewrote the dashboard-saved $key line"
done

# Values Compose already reads literally stay bare, so the plain grep/cut
# readers in later phases (_phase11_env_get, _phase12_env_get) still see them.
cat > "$tmp_dir/existing.env" <<'ENV'
LITELLM_KEY=sk#no-space-before-hash
LEMONADE_BASE_URL=http://lan-host:8000/api?model=a&stream=1
HERMES_LLM_API_KEY=it's-plain
ENV
for key in LITELLM_KEY LEMONADE_BASE_URL HERMES_LLM_API_KEY; do
    written="$key=$(dotenv_value "$(_env_get "$key" "")")"
    [[ "$written" == "$(grep -m1 "^${key}=" "$tmp_dir/existing.env")" ]] \
        && pass "rerun keeps Compose-literal $key bare for grep/cut readers" \
        || fail "rerun quoted Compose-literal $key: $written"
done

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    {
        printf '%s\n' 'services:' '  probe:' '    image: busybox' '    environment:'
        for key in "${keys[@]}"; do printf '      %s: ${%s}\n' "$key" "$key"; done
    } > "$tmp_dir/probe.yaml"
    if env -i PATH="$PATH" HOME="$HOME" docker compose --env-file "$tmp_dir/rerun.env" \
        -f "$tmp_dir/probe.yaml" config --format json > "$tmp_dir/probe.json" 2>"$tmp_dir/probe.err" \
        && python3 - "$tmp_dir/probe.json" "${keys[@]}" -- "${literals[@]}" <<'PY'
import json
import sys

environment = json.load(open(sys.argv[1], encoding="utf-8"))["services"]["probe"]["environment"]
args = sys.argv[2:]
split = args.index("--")
for key, literal in zip(args[:split], args[split + 1:]):
    observed = environment[key].replace("$$", "$")
    if observed != literal:
        sys.exit(f"{key}: expected {literal!r}, Compose resolved {observed!r}")
PY
    then
        pass "Docker Compose resolves every rerun value literally"
    else
        fail "Docker Compose did not resolve the rerun values literally: $(tr '\n' ' ' < "$tmp_dir/probe.err")"
    fi
fi

printf 'Results: %d passed, %d failed\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
