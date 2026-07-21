#!/usr/bin/env bash
# Regression coverage for the extension-audit Python interpreter boundary.

set -euo pipefail

if (( BASH_VERSINFO[0] < 4 )); then
    for modern_bash in /opt/homebrew/bin/bash /usr/local/bin/bash; do
        if [[ -x "$modern_bash" ]] && (( $("$modern_bash" -c 'echo "${BASH_VERSINFO[0]}"') >= 4 )); then
            exec "$modern_bash" "$0" "$@"
        fi
    done
    echo "[SKIP] ods-cli requires Bash 4+"
    exit 0
fi

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$TEST_DIR")"
SANDBOX="$(mktemp -d)"
trap 'rm -rf "$SANDBOX"' EXIT

good_python=""
for candidate in \
    "${ODS_TEST_PYTHON:-}" \
    "${HOME}/ods/.venv/installer-python/bin/python3" \
    python3 \
    python
do
    [[ -n "$candidate" ]] || continue
    resolved="$(command -v "$candidate" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || continue
    if "$resolved" -c 'import yaml' >/dev/null 2>&1; then
        good_python="$resolved"
        break
    fi
done

if [[ -z "$good_python" ]]; then
    echo "[SKIP] no Python interpreter with PyYAML is available"
    exit 0
fi

mkdir -p \
    "$SANDBOX/bin" \
    "$SANDBOX/install/.venv/installer-python/bin" \
    "$SANDBOX/install/scripts"
cp "$PROJECT_DIR/docker-compose.base.yml" "$SANDBOX/install/docker-compose.base.yml"
cp "$PROJECT_DIR/scripts/audit-extensions.py" "$SANDBOX/install/scripts/audit-extensions.py"
cat > "$SANDBOX/install/.venv/installer-python/bin/python3" <<'PYTHON'
#!/usr/bin/env bash
exec "$ODS_TEST_GOOD_PYTHON" "$@"
PYTHON
chmod +x "$SANDBOX/install/.venv/installer-python/bin/python3"

cat > "$SANDBOX/bin/python3" <<'PYTHON'
#!/usr/bin/env bash
echo "unexpected hardcoded python3 invocation" >&2
exit 93
PYTHON
chmod +x "$SANDBOX/bin/python3"

output="$({
    ODS_HOME="$SANDBOX/install" \
        ODS_TEST_GOOD_PYTHON="$good_python" \
        PATH="$SANDBOX/bin:$PATH" \
        "$BASH" "$PROJECT_DIR/ods-cli" audit --help
} 2>&1)" || {
    rc=$?
    printf '%s\n' "$output" >&2
    echo "ods audit --help exited $rc instead of using the ODS installer venv" >&2
    exit 1
}

grep -Fq 'Audit ODS extension manifests' <<< "$output"
echo "ODS_CLI_AUDIT_PYTHON_ROUTING_OK"
