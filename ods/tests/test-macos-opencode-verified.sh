#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stage=$(mktemp -d /tmp/ods-macos-opencode-test.XXXXXXXX)
trap 'rm -rf -- "$stage"' EXIT
mkdir -p "$stage/installers/lib"
python3 - "$ROOT/installers/macos/install-macos.sh" "$stage/function.sh" <<'PY'
import pathlib, re, sys
source = pathlib.Path(sys.argv[1]).read_text()
match = re.search(r'^_install_opencode\(\) \{.*?^\}', source, re.M | re.S)
assert match, 'Cannot locate OpenCode installation function'
pathlib.Path(sys.argv[2]).write_text(match.group(0))
PY
SOURCE_ROOT="$stage"
ODS_LOG_FILE="$stage/install.log"
export ODS_OPENCODE_TEST_STAGE="$stage"
cat > "$stage/installers/lib/verified-download.sh" <<'SH'
ods_install_verified_opencode() {
    printf 'verified helper\n' >> "$ODS_OPENCODE_TEST_STAGE/calls"
    printf 'fixture\n' > "$ODS_OPENCODE_TEST_STAGE/opencode"
    chmod 700 "$ODS_OPENCODE_TEST_STAGE/opencode"
}
SH
_find_opencode_bin() { [[ -x "$stage/opencode" ]] && printf '%s\n' "$stage/opencode"; }
ai() { :; }; ai_ok() { :; }; ai_warn() { :; }
brew() { printf 'unexpected brew\n' > "$stage/unverified"; return 1; }
curl() { printf 'unexpected curl\n' > "$stage/unverified"; return 1; }
source "$stage/function.sh"
_install_opencode
[[ "$OPENCODE_BIN" == "$stage/opencode" && ! -f "$stage/unverified" ]]
_install_opencode
[[ $(wc -l < "$stage/calls") -eq 1 ]]
printf '[PASS] macOS uses the verified helper and preserves an existing OpenCode install\n'
