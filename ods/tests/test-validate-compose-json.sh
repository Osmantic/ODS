#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
VALIDATOR="$ROOT_DIR/scripts/validate-compose-stack.sh"
FIXTURE="$(mktemp -d "${TMPDIR:-/tmp}/ods-compose-json.XXXXXX")"
trap 'rm -rf "$FIXTURE"' EXIT

mkdir -p "$FIXTURE/bin"
cat > "$FIXTURE/bin/docker" <<'SH'
#!/usr/bin/env bash
if [[ "${1:-}" == "compose" && "${2:-}" == "version" ]]; then
    exit 0
fi
if [[ "${1:-}" == "compose" && " $* " == *" config "* ]]; then
    if [[ "${COMPOSE_FIXTURE_FAIL:-0}" == "1" ]]; then
        echo "fixture compose error" >&2
        exit 17
    fi
    cat <<'YAML'
services:
  api:
    image: fixture/api
  web:
    image: fixture/web
YAML
    exit 0
fi
exit 2
SH
chmod +x "$FIXTURE/bin/docker"

success_json="$(PATH="$FIXTURE/bin:$PATH" bash "$VALIDATOR" --compose-flags '-f fixture.yml' --json)"
JSON_REPORT="$success_json" python3 - <<'PY'
import json
import os

report = json.loads(os.environ["JSON_REPORT"])
assert report == {
    "schema_version": "1",
    "kind": "compose-validation",
    "valid": True,
    "engine": "docker compose",
    "service_count": 2,
    "error_code": "",
}
PY

set +e
failure_json="$(
    PATH="$FIXTURE/bin:$PATH" COMPOSE_FIXTURE_FAIL=1 \
        bash "$VALIDATOR" --compose-flags '-f broken.yml' --json 2>"$FIXTURE/error.log"
)"
failure_status=$?
set -e
[[ $failure_status -eq 1 ]]
[[ "$(cat "$FIXTURE/error.log")" == *"fixture compose error"* ]]
JSON_REPORT="$failure_json" python3 - <<'PY'
import json
import os

report = json.loads(os.environ["JSON_REPORT"])
assert report["valid"] is False
assert report["engine"] == "docker compose"
assert report["service_count"] == 0
assert report["error_code"] == "compose_config_failed"
PY

echo "Compose validation JSON tests passed"
