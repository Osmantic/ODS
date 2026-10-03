#!/usr/bin/env bash
set -euo pipefail
# Deterministic regression for the 4.3.9 rollback-compat repair. The action-journal and
# vendor-secret helpers are deterministically bundled inside the single self-contained
# broker.py byte, so a packaged source/GitHub broker imports and secret-detects with ONLY
# broker.py present (no action_journal module and no vendor_secrets.py) -- matching the exact
# old-controller prestate that the broker-byte transaction must be able to restore.
SOURCE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT

import_and_check() {
  local broker_dir=$1
  python3 - "$broker_dir" <<'PY'
import importlib.util, sys
path = sys.argv[1]
spec = importlib.util.spec_from_file_location("pixel_broker", path + "/broker.py")
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
assert module.find_vendor_secret("sk-test1234567890123456789012345678901234") is not None
assert module.find_vendor_secret("Bearer YOUR_TOKEN_HERE") is None
assert module.find_vendor_secret("a benign sentence with no credential") is None
assert module.journal_sha256("x") == module.journal_sha256("x")
print("import + secret-detect ok")
PY
}

# Source broker: exact old-controller prestate, only broker.py present.
source_dir="$tmp/source"
mkdir -p "$source_dir"
cp "$SOURCE/deploy/source-broker/broker.py" "$source_dir/broker.py"
chmod 755 "$source_dir/broker.py"
[[ ! -e "$source_dir/action_journal" && ! -e "$source_dir/vendor_secrets.py" ]] \
  || { echo "source runtime unexpectedly carries a helper path" >&2; exit 1; }
[[ -z "$(find "$source_dir" -mindepth 1 ! -name 'broker.py' -print -quit)" ]] \
  || { echo "stray helper path in packaged source runtime" >&2; exit 1; }
import_and_check "$source_dir"

# GitHub broker: only broker.py present (self-contained single-file runtime).
github_dir="$tmp/github"
mkdir -p "$github_dir"
cp "$SOURCE/deploy/github-broker/broker.py" "$github_dir/broker.py"
chmod 755 "$github_dir/broker.py"
[[ ! -e "$github_dir/action_journal" && ! -e "$github_dir/vendor_secrets.py" ]] \
  || { echo "github runtime unexpectedly carries a helper path" >&2; exit 1; }
[[ -z "$(find "$github_dir" -mindepth 1 ! -name 'broker.py' -print -quit)" ]] \
  || { echo "stray helper path in packaged github runtime" >&2; exit 1; }
import_and_check "$github_dir"

echo "self-contained source/GitHub broker import + secret-detect regression passed"
