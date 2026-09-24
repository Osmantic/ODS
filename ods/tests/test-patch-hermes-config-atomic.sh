#!/usr/bin/env bash
# Production-function and CLI coverage for atomic Hermes config updates.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCHER="$ROOT_DIR/scripts/patch-hermes-config.py"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-hermes-config.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

cat > "$FIXTURE/config.yaml" <<'YAML'
model:
  default: "old-model"
  provider: "custom"
  base_url: "http://old.example/v1"
  context_length: 8192
providers:
  custom:
    request_timeout_seconds: 180
YAML
cp "$FIXTURE/config.yaml" "$FIXTURE/known-good.yaml"

python3 - "$PATCHER" "$FIXTURE/config.yaml" <<'PY'
import importlib.util
import sys
from pathlib import Path

script = Path(sys.argv[1])
config = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("patch_hermes_config", script)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

def fail_replace(_source, _target):
    raise OSError("injected publication failure")

module.os.replace = fail_replace
try:
    module.patch_config(config, "new-model", "http://new.example/v1", 16384)
except OSError as exc:
    assert "injected publication failure" in str(exc)
else:
    raise AssertionError("patch unexpectedly succeeded")

assert config.read_text(encoding="utf-8") == (config.parent / "known-good.yaml").read_text(encoding="utf-8")
assert list(config.parent.glob(".config.yaml.*.tmp")) == []
PY

ln -s "$FIXTURE/config.yaml" "$FIXTURE/live-config.yaml"
python3 "$PATCHER" "$FIXTURE/live-config.yaml" \
    --model new-model \
    --base-url http://new.example/v1 \
    --context-length 16384 >/dev/null

[[ -L "$FIXTURE/live-config.yaml" ]] || {
    echo "atomic patch replaced the configured symlink" >&2
    exit 1
}
grep -F 'default: "new-model"' "$FIXTURE/config.yaml" >/dev/null
grep -F 'base_url: "http://new.example/v1"' "$FIXTURE/config.yaml" >/dev/null
grep -F 'context_length: 16384' "$FIXTURE/config.yaml" >/dev/null

echo "Hermes config publication preserves old bytes on failure and symlink targets"
