#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

echo "[contract] OAuth credential provisioning helper"

tmpdir="$(mktemp -d)"
trap 'rm -rf "$tmpdir"' EXIT

helper_script="$ROOT_DIR/installers/lib/oauth-credentials.sh"

# 1. No bundled credentials directory
# Expected: helper exits successfully, no warning emitted
(
    source "$helper_script"
    output=$(copy_oauth_credentials "$tmpdir" 2>&1) || true
    if [[ "$output" == *"Bundled OAuth credentials directory is missing"* ]] || [[ "$output" == *"log_warn"* ]]; then
        echo "[FAIL] Helper should not emit a warning if the credentials directory is missing: $output"
        exit 1
    fi
)

# 2. Bundled credentials directory exists with a JSON file
# Expected: JSON copied into data/hermes
(
    source "$helper_script"
    mkdir -p "$tmpdir/extensions/services/hermes/credentials"
    echo '{"dummy": true}' > "$tmpdir/extensions/services/hermes/credentials/test-oauth.json"

    copy_oauth_credentials "$tmpdir" >/dev/null 2>&1

    if [[ ! -f "$tmpdir/data/hermes/test-oauth.json" ]]; then
        echo "[FAIL] Helper did not copy the JSON file into data/hermes"
        exit 1
    fi
)

# 3. Destination file already exists
# Expected: destination preserved, source not overwrite
(
    source "$helper_script"
    mkdir -p "$tmpdir/extensions/services/hermes/credentials"
    echo '{"new": true}' > "$tmpdir/extensions/services/hermes/credentials/test-exist.json"

    mkdir -p "$tmpdir/data/hermes"
    echo '{"existing": true}' > "$tmpdir/data/hermes/test-exist.json"

    copy_oauth_credentials "$tmpdir" >/dev/null 2>&1

    content=$(cat "$tmpdir/data/hermes/test-exist.json")
    if [[ "$content" != '{"existing": true}' ]]; then
        echo "[FAIL] Helper overwrote the existing destination file"
        exit 1
    fi
)

# 4. Sudo-aware permission simulation (uid 10000 mode 700)
# Expected: destination preserved, helper succeeds, no overwrite
(
    mkdir -p "$tmpdir/fakebin"
    cat > "$tmpdir/fakebin/sudo" <<EOF
#!/usr/bin/env bash
if [[ "\$1" == "-n" && "\$2" == "test" && "\$3" == "-f" ]]; then
    target="\$4"
    chmod 755 "$tmpdir/data/hermes"
    res=1
    if test -f "\$target"; then res=0; fi
    chmod 000 "$tmpdir/data/hermes"
    exit \$res
elif [[ "\$1" == "-n" && "\$2" == "cp" ]]; then
    exit 1
elif [[ "\$1" == "-n" && "\$2" == "chown" ]]; then
    exit 0
fi
exit 1
EOF
    chmod +x "$tmpdir/fakebin/sudo"

    mkdir -p "$tmpdir/extensions/services/hermes/credentials"
    echo '{"new": true}' > "$tmpdir/extensions/services/hermes/credentials/test-sudo.json"

    mkdir -p "$tmpdir/data/hermes"
    echo '{"existing": true}' > "$tmpdir/data/hermes/test-sudo.json"

    # We remove read/execute permissions to simulate host user not seeing inside
    chmod 000 "$tmpdir/data/hermes"

    # Run the helper in an environment where sudo is mocked via PATH
    PATH="$tmpdir/fakebin:$PATH" bash -c '
        source "'"$helper_script"'"
        copy_oauth_credentials "'"$tmpdir"'" 2>&1 || true
    ' > "$tmpdir/output.log" 2>&1

    output=$(cat "$tmpdir/output.log")

    # Restore permissions to check contents
    chmod 755 "$tmpdir/data/hermes"

    content=$(cat "$tmpdir/data/hermes/test-sudo.json")
    if [[ "$content" != '{"existing": true}' ]]; then
        echo "[FAIL] Helper overwrote the existing destination file during sudo simulation"
        exit 1
    fi

    if [[ "$output" != *"Preserved existing OAuth credential"* ]]; then
        echo "[FAIL] Helper did not log preservation success during sudo simulation: $output"
        exit 1
    fi
)

echo "[PASS] OAuth provisioning contracts"
