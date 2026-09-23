#!/usr/bin/env bash
set -euo pipefail
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
repo="$tmp/repo"
mkdir -p "$repo"
tar --exclude=.git --exclude=.env --exclude=.generated --exclude=.runtime --exclude=dist \
  --exclude=__pycache__ --exclude='*.pyc' -C "$SOURCE" -cf - . | tar -xf - -C "$repo"
configured="$tmp/install"
expected_other="$tmp/other-install"
mkdir -p "$configured" "$expected_other"
cat > "$repo/.env" <<ENV
PIXEL_INSTALL_DIR=$configured
PIXEL_RELEASE_VERSION=4.3.27
OPENCLAW_HOME=$tmp/openclaw
OPENCLAW_BIN=$tmp/openclaw-bin
ENV
mkdir -p "$tmp/openclaw"
cd "$repo"

# Keep the matching-install assertion independent of whether the outer CI host
# has Docker installed. Preflight only checks command availability before the
# deliberately non-executable OPENCLAW_BIN gate, so this private stub is never
# invoked and cannot mask any Docker behavior under test.
stub_bin="$tmp/stub-bin"
mkdir -p "$stub_bin"
printf '#!/usr/bin/env bash\nexit 125\n' > "$stub_bin/docker"
chmod 700 "$stub_bin/docker"

mismatch_output=$(bash scripts/verify.sh --expected-install-dir "$expected_other" 2>&1 || true)
grep -F "Configured PIXEL_INSTALL_DIR does not match --expected-install-dir" <<<"$mismatch_output" >/dev/null \
  || { echo "verify did not fail closed on a mismatched configured install" >&2; printf '%s\n' "$mismatch_output" >&2; exit 1; }

match_output=$(PATH="$stub_bin:$PATH" bash scripts/verify.sh --expected-install-dir "$configured" 2>&1 || true)
if grep -F "does not match --expected-install-dir" <<<"$match_output" >/dev/null; then
  echo "a matching configured install was rejected as mismatched" >&2; exit 1
fi
grep -F "OPENCLAW_BIN is not executable" <<<"$match_output" >/dev/null \
  || { echo "matching install did not proceed to the next ordinary verify gate (preflight)" >&2; printf '%s\n' "$match_output" >&2; exit 1; }

unknown_output=$(bash scripts/verify.sh --bogus 2>&1 || true)
grep -F "Unknown verify option" <<<"$unknown_output" >/dev/null \
  || { echo "verify accepted an unsupported argument" >&2; printf '%s\n' "$unknown_output" >&2; exit 1; }

echo "verify --expected-install-dir gate behaved correctly"
