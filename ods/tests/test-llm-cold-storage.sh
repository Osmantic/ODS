#!/bin/bash
# Contracts for scripts/llm-cold-storage.sh
#
# The archive path moves multi-GB model directories off the primary disk and
# leaves a symlink behind. If it reports success without moving anything, the
# operator believes they reclaimed space they did not reclaim.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COLD_STORAGE_SCRIPT="$SCRIPT_DIR/scripts/llm-cold-storage.sh"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

PASS=0
FAIL=0

pass() { echo -e "${GREEN}[PASS]${NC} $1"; PASS=$((PASS + 1)); }
skip() { echo -e "${YELLOW}[SKIP]${NC} $1"; }
fail() {
    echo -e "${RED}[FAIL]${NC} $1"
    [[ -n "${2:-}" ]] && echo "       $2"
    FAIL=$((FAIL + 1))
}

# Git Bash / MSYS turns `ln -s` on a directory into a junction, so `-L` is
# false there even though the path resolves. The archive path only ships on
# Linux and macOS; assert the link type where the script actually runs.
REAL_SYMLINKS=true
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) REAL_SYMLINKS=false ;;
esac

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# A stub `bc` that always fails, shadowing any real one. The script must not
# need it: bc is absent from a default Debian, Ubuntu Server, Fedora or Arch
# install, and the idle-day maths has to work without it.
STUB_BIN="$TMP_ROOT/stubbin"
mkdir -p "$STUB_BIN"
cat > "$STUB_BIN/bc" <<'STUB'
#!/bin/sh
echo "bc: stubbed out for this test" >&2
exit 127
STUB
chmod +x "$STUB_BIN/bc"

# Build a throwaway HF cache. $2 is the atime to stamp on the model file.
make_cache() {
    local root="$1" name="$2" atime="$3"
    mkdir -p "$root/hf/$name"
    echo "weights for $name" > "$root/hf/$name/model.bin"
    touch -a -d "$atime" "$root/hf/$name/model.bin"
}

run_cold_storage() {
    local root="$1"
    shift
    PATH="$STUB_BIN:$PATH" \
    HF_CACHE="$root/hf" \
    COLD_DIR="$root/cold" \
    LOG_FILE="$root/log/cold-storage.log" \
        bash "$COLD_STORAGE_SCRIPT" "$@"
}

# ---------------------------------------------------------------------------
# The script no longer shells out to bc
# ---------------------------------------------------------------------------
if grep -qE '\|[[:space:]]*bc([[:space:]]|$)' "$COLD_STORAGE_SCRIPT"; then
    fail "llm-cold-storage.sh still pipes into bc" \
         "$(grep -nE '\|[[:space:]]*bc([[:space:]]|$)' "$COLD_STORAGE_SCRIPT")"
else
    pass "idle-day maths does not depend on bc"
fi

# ---------------------------------------------------------------------------
# --execute archives an idle model even when the cold dir does not exist yet
# ---------------------------------------------------------------------------
CASE="$TMP_ROOT/archive"
make_cache "$CASE" "models--Idle--Model" "2020-01-01"
[[ ! -d "$CASE/cold" ]] || fail "fixture error: cold dir should not pre-exist"

OUT="$(run_cold_storage "$CASE" --execute 2>&1)" || true

if [[ -d "$CASE/cold/models--Idle--Model" ]]; then
    pass "--execute creates the cold storage directory and moves the model"
else
    fail "--execute did not move the model into cold storage" "$OUT"
fi

if ! $REAL_SYMLINKS; then
    skip "symlink type check (MSYS has no real symlinks)"
elif [[ -L "$CASE/hf/models--Idle--Model" ]]; then
    pass "a symlink is left behind in the HF cache"
else
    fail "no symlink left behind in the HF cache" "$(ls -l "$CASE/hf")"
fi

if [[ "$(cat "$CASE/hf/models--Idle--Model/model.bin" 2>/dev/null || true)" \
      == "weights for models--Idle--Model" ]]; then
    pass "the symlink still resolves to the model weights"
else
    fail "the symlink does not resolve to the model weights"
fi

if grep -q "ARCHIVED: models--Idle--Model" <<<"$OUT" \
   && grep -q "1 archived" <<<"$OUT"; then
    pass "the summary reports the archive that actually happened"
else
    fail "the summary did not report the archive" "$OUT"
fi

# ---------------------------------------------------------------------------
# A recently used model is left alone
# ---------------------------------------------------------------------------
CASE="$TMP_ROOT/recent"
make_cache "$CASE" "models--Hot--Model" "$(date '+%Y-%m-%d')"
OUT="$(run_cold_storage "$CASE" --execute 2>&1)" || true

if [[ -f "$CASE/hf/models--Hot--Model/model.bin" && ! -e "$CASE/cold/models--Hot--Model" ]]; then
    pass "a recently used model stays hot"
else
    fail "a recently used model was archived" "$OUT"
fi

if grep -q "0 archived" <<<"$OUT"; then
    pass "the summary reports nothing archived when nothing moved"
else
    fail "the summary miscounts an untouched scan" "$OUT"
fi

# ---------------------------------------------------------------------------
# Dry run (the default) never touches the cache
# ---------------------------------------------------------------------------
CASE="$TMP_ROOT/dryrun"
make_cache "$CASE" "models--Idle--Model" "2020-01-01"
OUT="$(run_cold_storage "$CASE" 2>&1)" || true

if [[ -f "$CASE/hf/models--Idle--Model/model.bin" && ! -d "$CASE/cold" ]]; then
    pass "the default dry run moves nothing and creates no cold directory"
else
    fail "the default dry run touched the filesystem" "$OUT"
fi

if grep -q "WOULD ARCHIVE: models--Idle--Model" <<<"$OUT"; then
    pass "the dry run names what it would archive"
else
    fail "the dry run did not name the candidate" "$OUT"
fi

# ---------------------------------------------------------------------------
# A name already present in cold storage is refused, not nested
# ---------------------------------------------------------------------------
CASE="$TMP_ROOT/collision"
make_cache "$CASE" "models--Idle--Model" "2020-01-01"
mkdir -p "$CASE/cold/models--Idle--Model"
echo "older copy" > "$CASE/cold/models--Idle--Model/model.bin"
OUT="$(run_cold_storage "$CASE" --execute 2>&1)" || true

if [[ ! -e "$CASE/cold/models--Idle--Model/models--Idle--Model" ]]; then
    pass "an existing cold copy is not nested inside itself"
else
    fail "the model was nested under the existing cold copy" "$OUT"
fi

if [[ -f "$CASE/hf/models--Idle--Model/model.bin" ]] && grep -q "leaving it hot" <<<"$OUT"; then
    pass "a collision leaves the model hot and says so"
else
    fail "a collision was not reported" "$OUT"
fi

if grep -q "0 archived" <<<"$OUT"; then
    pass "a refused archive is not counted as archived"
else
    fail "a refused archive was counted as archived" "$OUT"
fi

# ---------------------------------------------------------------------------
# Protected models are never archived
# ---------------------------------------------------------------------------
CASE="$TMP_ROOT/protected"
make_cache "$CASE" "models--BAAI--bge-base-en-v1.5" "2020-01-01"
OUT="$(run_cold_storage "$CASE" --execute 2>&1)" || true

if [[ -f "$CASE/hf/models--BAAI--bge-base-en-v1.5/model.bin" ]] \
   && grep -q "SKIP (protected)" <<<"$OUT"; then
    pass "a protected model is skipped even when idle"
else
    fail "a protected model was archived" "$OUT"
fi

# ---------------------------------------------------------------------------
# --restore puts an archived model back
# ---------------------------------------------------------------------------
CASE="$TMP_ROOT/restore"
make_cache "$CASE" "models--Idle--Model" "2020-01-01"
run_cold_storage "$CASE" --execute >/dev/null 2>&1 || true
OUT="$(run_cold_storage "$CASE" --restore "Idle/Model" 2>&1)" || true

if [[ -f "$CASE/hf/models--Idle--Model/model.bin" && ! -L "$CASE/hf/models--Idle--Model" ]] \
   && [[ ! -e "$CASE/cold/models--Idle--Model" ]]; then
    pass "--restore moves the model back into the HF cache"
else
    fail "--restore did not move the model back" "$OUT"
fi

echo ""
echo "Passed: $PASS  Failed: $FAIL"
[[ "$FAIL" -eq 0 ]]
echo "[PASS] llm-cold-storage contracts"
