#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-runtime-preflight.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

mkdir -p "$FIXTURE/lib" "$FIXTURE/bin" "$FIXTURE/scripts"
cp "$ROOT_DIR/ods-preflight.sh" "$FIXTURE/ods-preflight.sh"
cp "$ROOT_DIR/lib/safe-env.sh" "$FIXTURE/lib/safe-env.sh"
cp "$ROOT_DIR/scripts/linux-install-preflight.sh" "$FIXTURE/scripts/linux-install-preflight.sh"
printf 'GPU_BACKEND=cpu\n' > "$FIXTURE/.env"

cat > "$FIXTURE/bin/docker" <<'SH'
#!/usr/bin/env bash
case "${1:-}" in
    --version) echo 'Docker version 28.0.0, build fixture' ;;
    info) exit 0 ;;
    compose)
        [[ "${2:-}" == "version" ]] && echo 'Docker Compose version v2.35.0'
        ;;
    port|ps) exit 0 ;;
esac
SH
cat > "$FIXTURE/bin/curl" <<'SH'
#!/usr/bin/env bash
exit 1
SH
chmod +x "$FIXTURE/bin/docker" "$FIXTURE/bin/curl"

set +e
json_out="$(PATH="$FIXTURE/bin:$PATH" bash "$FIXTURE/ods-preflight.sh" --json)"
status=$?
set -e

[[ $status -eq 1 ]]
JSON_REPORT="$json_out" python3 - <<'PY'
import json
import os
from pathlib import Path

report = json.loads(os.environ["JSON_REPORT"])
assert report["schema_version"] == "1"
assert report["kind"] == "runtime-preflight"
assert report["backend"] == "cpu"
assert report["ready"] is False
assert report["summary"]["failed"] == 1
assert report["summary"]["passed"] >= 3
assert report["summary"]["warnings"] >= 4
assert len(report["checks"]) == sum(report["summary"].values())
assert any(item["status"] == "fail" and "LLM endpoint" in item["message"] for item in report["checks"])
assert Path(report["log_file"]).is_file()
PY

[[ "$json_out" != *"ODS Pre-flight Check"* ]]
echo "Runtime preflight JSON tests passed"
