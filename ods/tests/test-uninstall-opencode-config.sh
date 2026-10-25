#!/usr/bin/env bash
# ods-uninstall.sh removes only what ODS put in the owner's OpenCode config.
# The installer merges its llama-server provider into an existing
# ~/.config/opencode/opencode.json and keeps the owner's other providers, MCP
# servers and agents; uninstall (even with --keep-data) used to delete the
# whole file whenever it mentioned llama-server.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d -t ods-uninstall-opencode-XXXXXX)"
trap 'rm -rf "$TMP_DIR"' EXIT
FAILURES=0

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; FAILURES=$((FAILURES + 1)); }

command -v jq >/dev/null 2>&1 || { echo "[SKIP] jq is required"; exit 0; }

STUB_DIR="$TMP_DIR/bin"
mkdir -p "$STUB_DIR"
cat > "$STUB_DIR/docker" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${DOCKER_LOG:?}"
case "${1:-} ${2:-}" in
    "image inspect") exit "${DOCKER_IMAGE_INSPECT_EXIT:-0}" ;;
    "image rm") exit "${DOCKER_IMAGE_RM_EXIT:-0}" ;;
esac
exit 0
EOF
cat > "$STUB_DIR/systemctl" <<'EOF'
#!/usr/bin/env bash
[[ "${1:-}" != "is-enabled" ]]
EOF
cat > "$STUB_DIR/sudo" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "${SUDO_LOG:?}"
exit 0
EOF
cat > "$STUB_DIR/id" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    -u|-g) printf '1000\n' ;;
    -un) printf 'fixture-owner\n' ;;
    *) exit 1 ;;
esac
EOF
cat > "$STUB_DIR/pgrep" <<'EOF'
#!/usr/bin/env bash
exit 1
EOF
# Native Linux, so the WSL and macOS paths stay out of this fixture.
cat > "$STUB_DIR/uname" <<'EOF'
#!/usr/bin/env bash
case "${1:-}" in
    -r) printf 'fixture-native-kernel\n' ;;
    -s|"") printf 'Linux\n' ;;
    *) /usr/bin/uname "$@" ;;
esac
EOF
chmod +x "$STUB_DIR"/*

make_install() {
    local install_dir="$1"
    mkdir -p "$install_dir/data" "$install_dir/lib" "$install_dir/scripts" "$install_dir/systemd"
    cp "$ROOT_DIR/ods-uninstall.sh" "$install_dir/ods-uninstall.sh"
    cp "$ROOT_DIR/lib/safe-env.sh" "$ROOT_DIR/lib/system-uninstall.sh" "$install_dir/lib/"
    cp "$ROOT_DIR/scripts/compose-cache-policy.py" "$ROOT_DIR/scripts/resolve-compose-stack.sh" \
        "$install_dir/scripts/"
    printf 'import sys\nsys.exit(0)\n' > "$install_dir/scripts/uninstall-compose-volumes.py"
    touch "$install_dir/ods-cli" "$install_dir/docker-compose.base.yml"
    printf '%s\n' '-f docker-compose.base.yml' > "$install_dir/.compose-flags"
    printf '%s\n' 'GPU_BACKEND=cpu' > "$install_dir/.env"
}

# run_case NAME [uninstall options...]; the case's HOME is $TMP_DIR/home-NAME
run_case() {
    local name="$1" rc=0
    shift
    make_install "$TMP_DIR/$name"
    HOME="$TMP_DIR/home-$name" \
    INSTALL_DIR="$TMP_DIR/$name" \
    PATH="$STUB_DIR:$PATH" \
    DOCKER_LOG="$TMP_DIR/$name.docker" \
    SUDO_LOG="$TMP_DIR/$name.sudo" \
    ODS_UNINSTALL_SYSTEMD_DIR="$TMP_DIR/$name/systemd" \
        bash "$TMP_DIR/$name/ods-uninstall.sh" --force "$@" >"$TMP_DIR/$name.out" 2>&1 || rc=$?
    [[ "$rc" == 0 ]] || fail "$name: uninstall exited $rc: $(tail -5 "$TMP_DIR/$name.out")"
}

ods_only='{
  "$schema": "https://opencode.ai/config.json",
  "model": "llama-server/qwen",
  "small_model": "llama-server/qwen",
  "agent": {"build": {"model": "llama-server/qwen"}, "plan": {"model": "llama-server/qwen"}},
  "provider": {"llama-server": {"npm": "@ai-sdk/openai-compatible", "name": "ODS",
    "options": {"baseURL": "http://127.0.0.1:4000/v1", "apiKey": "sk-ods"}, "models": {"qwen": {}}}}
}'

# 1. A config only ODS wrote is removed, as before.
dir="$TMP_DIR/home-ods-only/.config/opencode"
mkdir -p "$dir"
printf '%s\n' "$ods_only" > "$dir/opencode.json"
cp "$dir/opencode.json" "$dir/config.json"
run_case ods-only
[[ ! -e "$dir/opencode.json" && ! -e "$dir/config.json" ]] \
    && pass "a config holding only ODS's provider is removed" \
    || fail "ODS-only OpenCode config was left behind"

# 2. An owner's config that the installer merged into keeps the owner's parts.
dir="$TMP_DIR/home-owner/.config/opencode"
mkdir -p "$dir"
jq '.provider.openrouter = {"options": {"apiKey": "sk-or-OWNER"}}
    | .mcp = {"github": {"type": "local", "command": ["gh-mcp"]}}
    | .agent.plan.model = "openrouter/anthropic/claude"
    | .agent.review = {"prompt": "OWNER review agent"}' <<<"$ods_only" > "$dir/opencode.json"
chmod 600 "$dir/opencode.json"
cp -p "$dir/opencode.json" "$dir/config.json"
run_case owner --keep-data
for file in opencode.json config.json; do
    if [[ ! -f "$dir/$file" ]]; then
        fail "owner $file was deleted"
        continue
    fi
    jq -e '.provider.openrouter.options.apiKey == "sk-or-OWNER"
        and .mcp.github.command == ["gh-mcp"]
        and .agent.plan.model == "openrouter/anthropic/claude"
        and .agent.review.prompt == "OWNER review agent"' "$dir/$file" >/dev/null \
        && pass "owner $file keeps its own provider, MCP server and agents" \
        || fail "owner $file lost owner settings: $(cat "$dir/$file")"
    jq -e '(.provider | has("llama-server") | not) and (has("model") | not)
        and (has("small_model") | not) and (.agent | has("build") | not)' "$dir/$file" >/dev/null \
        && pass "owner $file no longer points at ODS's llama-server" \
        || fail "owner $file still holds ODS settings: $(cat "$dir/$file")"
    [[ "$(ls -l "$dir/$file" | cut -c1-10)" == "-rw-------" ]] \
        && pass "owner $file keeps its private mode" \
        || fail "owner $file mode changed to $(ls -l "$dir/$file" | cut -c1-10)"
done

# 3. A config jq cannot parse (OpenCode accepts JSONC) is left untouched.
dir="$TMP_DIR/home-jsonc/.config/opencode"
mkdir -p "$dir"
printf '{\n  // my providers\n  "model": "llama-server/qwen",\n}\n' > "$dir/opencode.json"
cp "$dir/opencode.json" "$TMP_DIR/jsonc.before"
run_case jsonc
cmp -s "$dir/opencode.json" "$TMP_DIR/jsonc.before" \
    && pass "an unparseable owner config is kept unchanged" \
    || fail "unparseable OpenCode config was changed or removed"
grep -q "remove its llama-server provider by hand" "$TMP_DIR/jsonc.out" \
    && pass "uninstall says the unparseable config needs a manual edit" \
    || fail "no manual-edit notice for the unparseable config"

[[ "$FAILURES" == 0 ]] || exit 1
echo "All OpenCode uninstall checks passed"
