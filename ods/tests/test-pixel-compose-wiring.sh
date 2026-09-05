#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EDGE="$ROOT/extensions/services/pixel-edge/compose.yaml.disabled"
BASE="$ROOT/docker-compose.base.yml"

[[ -f "$EDGE" && ! -e "$ROOT/extensions/services/pixel-edge/compose.yaml" ]] || {
    echo "Pixel Edge must ship disabled until the qualified Linux installer enables it" >&2
    exit 1
}

python3 - "$EDGE" <<'PY'
import pathlib, re, sys

text = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
assert "PIXEL_GATEWAY_TOKEN" not in text and "PIXEL_OPERATOR_TOKEN" not in text
edge = text.split("\n  open-webui:", 1)[0]
assert not re.search(r"(?m)^    ports:", edge)
assert "network_mode: host" not in edge
assert "networks:\n      - default" in edge
assert "http://pixel-edge:9595/v1;${OPEN_WEBUI_LLM_BASE_URL:-${LLM_API_URL:-http://llama-server:8080}/v1}" in text
assert "${PIXEL_OPENWEBUI_KEY:?Set PIXEL_OPENWEBUI_KEY in .env};${OPEN_WEBUI_LLM_API_KEY:-}" in text
assert "PIXEL_PREVIEW_PROXY_KEY=${DASHBOARD_API_KEY:?Set DASHBOARD_API_KEY in .env}" in text
assert "PIXEL_PREVIEW_SOCKET=/pixel-preview-runtime/http.sock" in text
assert "${PIXEL_PREVIEW_RUNTIME_DIR:?Set PIXEL_PREVIEW_RUNTIME_DIR in .env}:/pixel-preview-runtime:ro" in text
assert 'TASK_MODEL_EXTERNAL: "${OPEN_WEBUI_TASK_MODEL:-${GGUF_FILE:-${LLM_MODEL:-default}}}"' in text
for required in (
    'ENABLE_OPENAI_API: "true"',
    'DEFAULT_MODELS: "pixel/default"',
    'DEFAULT_PINNED_MODELS: "pixel/default"',
    "DEFAULT_PROMPT_SUGGESTIONS:",
    "TASK_MODEL_EXTERNAL:",
    'ENABLE_TITLE_GENERATION: "false"',
    'ENABLE_TAGS_GENERATION: "false"',
    'ENABLE_FOLLOW_UP_GENERATION: "false"',
    'PIXEL_EDGE_URL: "http://pixel-edge:9595"',
):
    assert required in text, required
webui = text.split("\n  open-webui:", 1)[1]
webui = re.split(r"\n  [a-zA-Z0-9_-]+:", webui, maxsplit=1)[0]
assert re.search(r"depends_on:\s+pixel-edge:\s+condition: service_healthy", webui)
for service in ("dashboard-api", "dashboard"):
    marker = f"\n  {service}:"
    if marker not in text:
        continue
    block = text.split(marker, 1)[1]
    block = re.split(r"\n  [a-zA-Z0-9_-]+:", block, maxsplit=1)[0]
    assert "depends_on:" not in block
PY

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    runtime="$(mktemp -d)"
    cleanup() { case "$runtime" in /tmp/*|/var/tmp/*) rm -rf -- "$runtime" ;; esac; }
    trap cleanup EXIT
    PIXEL_OPENWEBUI_KEY="$(printf 'a%.0s' {1..64})" \
    PIXEL_INGRESS_GID=1234 \
    PIXEL_INGRESS_RUNTIME_DIR="$runtime" \
    PIXEL_PREVIEW_RUNTIME_DIR="$runtime" \
    DASHBOARD_API_KEY="$(printf 'c%.0s' {1..64})" \
    WEBUI_SECRET="$(printf 'b%.0s' {1..64})" \
        docker compose -f "$BASE" -f "$EDGE" config --quiet
    PIXEL_OPENWEBUI_KEY="$(printf 'a%.0s' {1..64})" \
    PIXEL_INGRESS_GID=1234 \
    PIXEL_INGRESS_RUNTIME_DIR="$runtime" \
    PIXEL_PREVIEW_RUNTIME_DIR="$runtime" \
    DASHBOARD_API_KEY="$(printf 'c%.0s' {1..64})" \
    WEBUI_SECRET="$(printf 'b%.0s' {1..64})" \
    GGUF_FILE="Qwen-Test-Q4_K_M.gguf" \
    LLM_MODEL="qwen-test" \
        docker compose -f "$BASE" -f "$EDGE" config --format json > "$runtime/config.json"
    python3 - "$runtime/config.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
edge = value["services"]["pixel-edge"]
assert not edge.get("ports")
assert edge.get("network_mode") != "host"
assert set(edge["networks"]) == {"default"}
assert edge["environment"]["PIXEL_PREVIEW_PROXY_KEY"] == "c" * 64
assert edge["environment"]["PIXEL_PREVIEW_SOCKET"] == "/pixel-preview-runtime/http.sock"
assert any(mount["target"] == "/pixel-preview-runtime" and mount["read_only"] for mount in edge["volumes"])
webui = value["services"]["open-webui"]
assert webui["depends_on"]["pixel-edge"]["condition"] == "service_healthy"
assert webui["environment"]["OPENAI_API_BASE_URLS"].startswith("http://pixel-edge:9595/v1;")
assert webui["environment"]["DEFAULT_MODELS"] == "pixel/default"
suggestions = json.loads(webui["environment"]["DEFAULT_PROMPT_SUGGESTIONS"])
assert [item["title"][0] for item in suggestions] == [
    "Check ODS health", "Build something", "Research a topic", "Plan a complex task",
]
assert all(len(item["title"]) == 2 and item["content"] for item in suggestions)
assert webui["environment"]["TASK_MODEL_EXTERNAL"] == "Qwen-Test-Q4_K_M.gguf"
assert webui["environment"]["ENABLE_TITLE_GENERATION"] == "false"
assert webui["environment"]["ENABLE_TAGS_GENERATION"] == "false"
assert webui["environment"]["ENABLE_FOLLOW_UP_GENERATION"] == "false"
dashboard = value["services"]["dashboard-api"]
assert "pixel-edge" not in dashboard.get("depends_on", {})
ui = value["services"]["dashboard"]
assert "pixel-edge" not in ui.get("depends_on", {})
PY
    PIXEL_OPENWEBUI_KEY="$(printf 'a%.0s' {1..64})" \
    PIXEL_INGRESS_GID=1234 \
    PIXEL_INGRESS_RUNTIME_DIR="$runtime" \
    PIXEL_PREVIEW_RUNTIME_DIR="$runtime" \
    DASHBOARD_API_KEY="$(printf 'c%.0s' {1..64})" \
    WEBUI_SECRET="$(printf 'b%.0s' {1..64})" \
    GGUF_FILE="Qwen-Test-Q4_K_M.gguf" \
    LLM_MODEL="qwen-test" \
    OPEN_WEBUI_TASK_MODEL="ods/current" \
        docker compose -f "$BASE" -f "$EDGE" config --format json > "$runtime/config-explicit.json"
    python3 - "$runtime/config-explicit.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["services"]["open-webui"]["environment"]["TASK_MODEL_EXTERNAL"] == "ods/current"
PY
fi

echo "Pixel Compose wiring checks passed"
