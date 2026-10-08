#!/usr/bin/env bash
# Builds whose context is the install root must not read the whole install.
# The classic builder (used when the buildx plugin is missing) stats every file
# in the context and fails on data/ directories owned by container users. The
# root .dockerignore must exclude everything except the paths these Dockerfiles
# copy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DOCKERFILES=(
    extensions/services/pixel-inference/Dockerfile
    extensions/services/remote-provider-egress/Dockerfile
    extensions/services/remote-provider-ssh-tunnel/Dockerfile
)
# Every shipped Compose file is read with the install root as its project
# directory, so `context: .` in any of them (enabled or .disabled) is the
# install root. Library extensions are not listed: the resolver rewrites
# their `context: .` to the extension's own directory.
# A file that names another Compose project runs on its own, with contexts
# relative to its own directory (the Windows standalone ComfyUI).
COMPOSE_FILES=()
shopt -s nullglob
for compose_file in "$ROOT_DIR"/docker-compose*.yml "$ROOT_DIR"/extensions/services/*/compose*.yaml*; do
    project="$(awk '/^name:/ { print $2; exit }' "$compose_file")"
    [[ -z "$project" || "$project" == ods ]] && COMPOSE_FILES+=("$compose_file")
done
shopt -u nullglob

PASSED=0
FAILED=0
pass() { echo "[PASS] $1"; PASSED=$((PASSED + 1)); }
fail() { echo "[FAIL] $1"; FAILED=$((FAILED + 1)); }

# Every Dockerfile built from the install root must be listed above.
root_context_dockerfiles="$(awk '
    FNR == 1 { root = 0 }
    /^[[:space:]]+context:[[:space:]]*\.[[:space:]]*$/ { root = 1; next }
    root && /^[[:space:]]+dockerfile:/ { print $2; root = 0; next }
    /^[[:space:]]+[a-z_]+:/ { root = 0 }
' "${COMPOSE_FILES[@]}" | sort -u)"
expected="$(printf '%s\n' "${DOCKERFILES[@]}" | sort)"
if [[ "$root_context_dockerfiles" == "$expected" ]]; then
    pass "the root-context builds are the ones this test covers"
else
    fail "root-context builds changed; update DOCKERFILES and .dockerignore: $(printf '%s ' $root_context_dockerfiles)"
fi

sources=()
for dockerfile in "${DOCKERFILES[@]}"; do
    while IFS= read -r src; do
        sources+=("$src")
    done < <(awk '$1 == "COPY" && $2 !~ /^--from/ { print $2 }' "$ROOT_DIR/$dockerfile")
done

if grep -qx '\*' "$ROOT_DIR/.dockerignore"; then
    pass ".dockerignore excludes everything by default"
else
    fail ".dockerignore must start from '*' so data/ is never sent"
fi
for src in "${sources[@]}"; do
    if grep -qxF "!$src" "$ROOT_DIR/.dockerignore"; then
        pass ".dockerignore admits $src"
    else
        fail ".dockerignore does not admit $src, which a root-context Dockerfile copies"
    fi
done

# Behavioural: the classic builder against an install-shaped context with an
# unreadable data/ directory. A FROM scratch Dockerfile with the same COPY
# lines checks the context without pulling or running anything.
if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
    echo "[SKIP] Docker unavailable; classic-builder check skipped"
else
    TMP_DIR="$(mktemp -d)"
    trap 'chmod -R u+rwx "$TMP_DIR"; rm -rf "$TMP_DIR"' EXIT
    cp "$ROOT_DIR/.dockerignore" "$TMP_DIR/"
    for src in "${sources[@]}"; do
        mkdir -p "$TMP_DIR/$(dirname "$src")"
        cp -R "$ROOT_DIR/$src" "$TMP_DIR/$src"
    done
    mkdir -p "$TMP_DIR/data/container-owned" "$TMP_DIR/data/models"
    echo private > "$TMP_DIR/data/container-owned/state"
    chmod 000 "$TMP_DIR/data/container-owned"
    head -c 1048576 /dev/zero > "$TMP_DIR/data/models/model.gguf"
    for dockerfile in "${DOCKERFILES[@]}"; do
        probe="$TMP_DIR/probe.Dockerfile"
        { echo "FROM scratch"; awk '$1 == "COPY" && $2 !~ /^--from/' "$ROOT_DIR/$dockerfile"; } > "$probe"
        if out="$(DOCKER_BUILDKIT=0 docker build -q -f "$probe" "$TMP_DIR" 2>&1)"; then
            pass "classic builder builds $dockerfile's context with unreadable data/"
            docker image rm -f "${out##*$'\n'}" >/dev/null 2>&1 || echo "[WARN] could not remove probe image"
        else
            fail "classic builder failed for $dockerfile: $(printf '%s' "$out" | tail -1)"
        fi
    done
fi

echo "Result: $PASSED passed, $FAILED failed"
[[ $FAILED -eq 0 ]]
