#!/usr/bin/env bash
# Model-profile smoke (PLAN WP3). CI only (.github/workflows/model-profile-smoke.yml):
# it pulls and runs the pinned llama.cpp CPU image, so never run it on a host
# with a live ODS stack.
#
# Renders the CPU stack, serves two SHA-pinned GGUFs with its llama-server
# launch, and runs the profile battery (python3 -m model_profile) on each.
# Only the deterministic parts are asserted:
# - Qwen3-0.6B: the chat probe passes, /props reports tool-call support,
#   /apply-template renders a tool into the prompt, the thinking control is
#   enable_thinking, and the tool-call probe's class repeats across two runs
#   (temperature 0, fixed seed). Whether so small a model calls the tool is
#   printed, not judged.
# - stories260K, which has no chat template: the battery completes without a
#   crash and does not report a working chat model.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

# Qwen/Qwen3-0.6B-GGUF at a fixed revision: its only GGUF, 639,446,688 bytes.
QWEN_FILE="Qwen3-0.6B-Q8_0.gguf"
QWEN_URL="https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/$QWEN_FILE"
QWEN_SHA256="9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
# The tiny model test-amd-cpu-smoke.sh pins; it has no chat template.
STORIES_FILE="stories260K-f32.gguf"
STORIES_URL="https://huggingface.co/ggml-org/test-model-stories260K/resolve/479896ec924af6d40fd419ab8f4d1eb2101de00d/$STORIES_FILE"
STORIES_SHA256="270cba1bd5109f42d03350f60406024560464db173c0e387d91f0426d3bd256d"
MODEL_CACHE="${MODEL_PROFILE_SMOKE_CACHE:-$HOME/.cache/ods-model-profile-smoke}"
PORT="${MODEL_PROFILE_SMOKE_PORT:-18090}"
# CPU inference is slower than the 120-second activation budget assumes.
BUDGET_SECONDS=300
CONTAINER="ods-model-profile-smoke-$$"
WORK_DIR="$(mktemp -d)"

cleanup() {
    local status=$?
    if [[ "$status" -ne 0 ]] && docker container inspect "$CONTAINER" >/dev/null 2>&1; then
        echo "--- llama-server log" >&2
        docker logs --tail 80 "$CONTAINER" >&2 || echo "(log unavailable)" >&2
    fi
    # A container that never started is not an error here.
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    rm -rf "$WORK_DIR"
    exit "$status"
}
trap cleanup EXIT

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

fetch() {
    local file="$1" url="$2" sha="$3"
    local path="$MODEL_CACHE/$file"
    mkdir -p "$MODEL_CACHE"
    # A cached copy is used only when it still matches its pin.
    if [[ -f "$path" ]] && echo "$sha  $path" | sha256sum -c - >/dev/null 2>&1; then
        pass "$file is cached and matches its pinned SHA-256"
        return
    fi
    curl -fsSL --retry 3 -o "$path.part" "$url"
    echo "$sha  $path.part" | sha256sum -c - >/dev/null || fail "$file does not match its pinned SHA-256"
    mv "$path.part" "$path"
    pass "$file matches its pinned SHA-256"
}

# Placeholders for the base stack's required secrets; nothing here starts it.
export WEBUI_SECRET=ci-placeholder SEARXNG_SECRET=ci-placeholder \
    N8N_USER=ci@example.com N8N_PASS=ci-placeholder LITELLM_KEY=ci-placeholder
unset LLAMA_SERVER_IMAGE

render() {
    GGUF_FILE="$1" docker compose -f docker-compose.base.yml -f docker-compose.cpu.yml \
        config --format json > "$WORK_DIR/cpu.json"
    python3 - "$WORK_DIR" <<'PY'
import json
import sys
from pathlib import Path

work = Path(sys.argv[1])
spec = json.loads((work / "cpu.json").read_text())["services"]["llama-server"]
(work / "image").write_text(spec["image"] + "\n")
(work / "args").write_text("".join(f"{arg}\n" for arg in spec.get("command") or []))
(work / "env").write_text("".join(f"{key}={value}\n" for key, value in (spec.get("environment") or {}).items()
                                  if value is not None))
PY
}

serve() {
    local file="$1"
    render "$file"
    local image
    image="$(cat "$WORK_DIR/image")"
    mapfile -t args < "$WORK_DIR/args"
    # Replace the previous model's server, if any.
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker pull --quiet "$image" >/dev/null
    docker run -d --name "$CONTAINER" --security-opt no-new-privileges:true \
        -p "127.0.0.1:$PORT:8080" --env-file "$WORK_DIR/env" \
        -v "$MODEL_CACHE:/models:ro" "$image" "${args[@]}" >/dev/null
    : > "$WORK_DIR/health"
    for _ in $(seq 1 120); do
        # Refused or 503 until the model is loaded; the loop bounds the wait.
        if curl -fsS "http://127.0.0.1:$PORT/health" >"$WORK_DIR/health" 2>/dev/null; then
            break
        fi
        docker container inspect -f '{{.State.Running}}' "$CONTAINER" | grep -qx true \
            || fail "llama-server exited before it served $file"
        sleep 2
    done
    grep -q '"ok"' "$WORK_DIR/health" || fail "/health did not report ok for $file"
    pass "the CPU launch serves $file"
}

battery() {
    PYTHONPATH="$ROOT_DIR/bin" python3 -m model_profile --url "http://127.0.0.1:$PORT" \
        --budget "$BUDGET_SECONDS" > "$WORK_DIR/$1.json" || fail "the profile battery crashed ($1)"
}

echo "== Fetch the pinned models"
fetch "$QWEN_FILE" "$QWEN_URL" "$QWEN_SHA256"
fetch "$STORIES_FILE" "$STORIES_URL" "$STORIES_SHA256"

echo "== Qwen3-0.6B: a chat model with a tool-calling, thinking template"
serve "$QWEN_FILE"
base="http://127.0.0.1:$PORT"
curl -fsS "$base/apply-template" -H 'Content-Type: application/json' \
    -d '{"messages": [{"role": "user", "content": "What is the weather in Paris?"}],
         "tools": [{"type": "function", "function": {"name": "get_weather",
                    "description": "Get the current weather for a city.",
                    "parameters": {"type": "object", "properties": {"city": {"type": "string"}},
                                   "required": ["city"]}}}]}' > "$WORK_DIR/applied.json"
battery qwen-1
battery qwen-2
python3 - "$WORK_DIR" <<'PY' || fail "Qwen3-0.6B's profile breaks a deterministic expectation"
import json
import sys
from pathlib import Path

work = Path(sys.argv[1])
first, second = (json.loads((work / f"qwen-{run}.json").read_text()) for run in (1, 2))
applied = json.loads((work / "applied.json").read_text())
errors = []
if first["probes"]["P1"]["status"] != "pass":
    errors.append(f"P1 (chat) did not pass: {first['probes']['P1']}")
if first["facts"]["templateCaps"].get("supports_tool_calls") is not True:
    errors.append(f"/props must report tool-call support: {first['facts']['templateCaps']}")
if "get_weather" not in str(applied.get("prompt", "")):
    errors.append("/apply-template did not render the tool into the prompt")
if first["facts"].get("thinkingControl") != "enable_thinking":
    errors.append(f"thinking control must be enable_thinking: {first['facts'].get('thinkingControl')}")
classes = [(run["probes"]["P2"]["status"], run["probes"]["P2"]["failureClass"]) for run in (first, second)]
if classes[0] != classes[1]:
    errors.append(f"the tool-call probe changed class between identical runs: {classes}")
print(json.dumps({"tools": classes[0], "summary": first["summary"], "elapsedMs": first["elapsedMs"]}))
if errors:
    print("\n".join(f"[FAIL] {error}" for error in errors), file=sys.stderr)
    sys.exit(1)
PY
pass "Qwen3-0.6B: chat passes, tools and thinking are detected, the tool-call class repeats"

echo "== stories260K: no chat template"
serve "$STORIES_FILE"
battery stories
python3 - "$WORK_DIR/stories.json" <<'PY' || fail "the no-template profile is malformed"
import json
import sys

result = json.load(open(sys.argv[1]))
probes = result["probes"]
ok = (set(probes) == {"P1", "P2", "P3", "P4", "P5", "P6", "P8"}
      and result["summary"]["chat"] is not True)
print(json.dumps({"summary": result["summary"], "status": result["status"]}))
sys.exit(0 if ok else 1)
PY
pass "stories260K: the battery completes and reports no working chat model"

echo "Model profile smoke passed"
