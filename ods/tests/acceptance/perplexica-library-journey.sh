#!/usr/bin/env bash
# Disposable installed proof: lean ODS -> Perplexica + SearXNG -> research -> re-add.
set -euo pipefail

product="${ODS_ACCEPTANCE_PRODUCT_ROOT:?product checkout is required}"
harness="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
audit_root="${RUNNER_TEMP:?runner temp is required}/ods-perplexica-library"
export INSTALL_DIR="$audit_root/install"
export LOG_FILE="$audit_root/install.log"
key_file="$audit_root/mock.key"
mock_port=18080
mock_pid=""

cleanup() {
    if [[ -n "$mock_pid" ]]; then
        kill "$mock_pid" 2>/dev/null || true
        wait "$mock_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT

fail() {
    printf 'FAIL: %s\n' "$*" >&2
    docker ps --format '{{.Names}} {{.Status}}' >&2 || true
    if [[ -f "$audit_root/mock.log" ]]; then
        printf 'Mock completion stages (request text and credentials omitted):\n' >&2
        grep -E 'accept=|reject=' "$audit_root/mock.log" | tail -n 50 >&2 || true
    fi
    # Vane can leave non-streaming /api/search open if its background agent
    # rejects. Keep only error-shaped lines and redact all generated secrets.
    if docker ps --format '{{.Names}}' | grep -Fxq ods-perplexica; then
        python3 - "$INSTALL_DIR/.env" "$key_file" <<'PY' >&2 || true
import os, re, subprocess, sys
secrets = []
if os.path.isfile(sys.argv[1]):
    for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.rstrip("\n").split("=", 1)
        if any(word in key.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            value = value.strip().strip("\"'")
            if len(value) >= 8:
                secrets.append(value)
if os.path.isfile(sys.argv[2]):
    secrets.append(open(sys.argv[2], encoding="ascii").read().strip())
result = subprocess.run(["docker", "logs", "--tail", "150", "ods-perplexica"],
                        capture_output=True, text=True, timeout=15, check=False)
print("Perplexica error diagnostics (secrets redacted):")
for line in (result.stdout + result.stderr).splitlines():
    if not re.search(r"error|fail|reject|timeout|fetch|download|unhandled", line, re.I):
        continue
    for secret in secrets:
        line = line.replace(secret, "[redacted]")
    line = re.sub(r"(?i)(bearer\s+)\S+", r"\1[redacted]", line)
    print(line[:700])
PY
    fi
    exit 1
}

[[ "${GITHUB_ACTIONS:-}" == true ]] || fail 'refusing non-disposable host'
[[ "$RUNNER_TEMP" == /* && "$INSTALL_DIR" == "$RUNNER_TEMP"/* ]] || fail 'install path is outside runner temp'
[[ "$(git -C "$(dirname "$product")" rev-parse HEAD)" == 3a8c351aef8782cc2cd29029e69d3195571a2c38 ]] \
    || fail 'product source changed'
[[ "$(cat /proc/1/comm)" == systemd ]] || fail 'runner is not a systemd host'
docker info >/dev/null || fail 'isolated Docker Engine unavailable'
[[ ! -e "$INSTALL_DIR" ]] || fail 'fresh install path is not empty'
if docker ps -a --format '{{.Names}}' | grep -Eq '^ods-'; then
    fail 'pre-existing ODS containers on runner'
fi

umask 077
mkdir -p "$audit_root"
python3 - "$key_file" <<'PY'
import secrets, sys
with open(sys.argv[1], "w", encoding="ascii") as stream:
    stream.write("mock-" + secrets.token_hex(24))
PY
chmod 600 "$key_file"
python3 "$harness/mock-openai-upstream.py" --key-file "$key_file" \
    --port "$mock_port" >"$audit_root/mock.log" 2>&1 &
mock_pid=$!
for _ in {1..30}; do
    curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS --max-time 2 "http://127.0.0.1:$mock_port/healthz" >/dev/null \
    || fail 'mock upstream did not start'

if ! (umask 022; cd "$product" && timeout 2400s bash install-core.sh \
    --non-interactive --skip-docker --no-bootstrap \
    --external-llm-url "http://127.0.0.1:$mock_port" \
    --external-llm-provider openai-compatible \
    --external-llm-model ods-acceptance-mock \
    --external-llm-key-file "$key_file") >>"$LOG_FILE" 2>&1; then
    fail 'exact product installer did not finish'
fi

for service in perplexica searxng; do
    [[ -f "$INSTALL_DIR/extensions/services/$service/compose.yaml.disabled" ]] \
        || fail "fresh lean install selected $service"
done
if docker ps -a --format '{{.Names}}' | grep -Fxq ods-llama-server; then
    fail 'external model install launched a managed llama-server'
fi
if docker image ls --format '{{.Repository}}' | grep -Eq '^itzcrazykns1337/vane$'; then
    fail 'fresh lean install pulled the unselected Vane image'
fi
printf 'PASS: fresh external-model ODS omitted Perplexica, SearXNG, and managed llama-server\n'

catalog_row() {
    curl -fsS --max-time 30 http://127.0.0.1:3001/api/extensions/catalog \
        | python3 -c 'import json,sys; item=next(x for x in json.load(sys.stdin)["extensions"] if x["id"]=="perplexica"); print(json.dumps({k:item.get(k) for k in ("source","status","library_manageable","library_selected")}))'
}
before="$(catalog_row)" || fail 'Library catalog unavailable before Add'
python3 - "$before" <<'PY' || fail 'Perplexica was not Available for Add'
import json,sys
row=json.loads(sys.argv[1])
assert row=={"source":"core","status":"disabled","library_manageable":True,"library_selected":False}, row
PY

api_instance="$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" \
    || fail 'Dashboard API was not running before Add'
enable_perplexica() {
    local response="$1" expected_deps="$2" code health
    code="$(curl -sS --max-time 1200 -o "$response" -w '%{http_code}' \
        -X POST 'http://127.0.0.1:3001/api/extensions/perplexica/enable?auto_enable_deps=true')" \
        || fail 'Perplexica Add request was unreachable'
    [[ "$code" == 200 ]] || fail "Perplexica Add returned HTTP $code"
    python3 - "$response" <<'PY' || fail 'Perplexica Add reported failure'
import json,sys
value=json.load(open(sys.argv[1],encoding="utf-8"))
assert "perplexica" in value.get("enabled_services",[]), value
assert value.get("failed_services")==[], value
PY
    if [[ "$expected_deps" == yes ]]; then
        python3 - "$response" <<'PY' || fail 'Perplexica Add did not activate SearXNG'
import json,sys
value=json.load(open(sys.argv[1],encoding="utf-8"))
assert "searxng" in value.get("enabled_services",[]), value
PY
    fi
    for service in searxng perplexica; do
        [[ -f "$INSTALL_DIR/extensions/services/$service/compose.yaml" ]] \
            || fail "$service fragment was not selected"
        health=""
        for _ in {1..90}; do
            health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "ods-$service" 2>/dev/null || true)"
            [[ "$health" == healthy ]] && break
            [[ "$health" == unhealthy ]] && fail "$service became unhealthy"
            sleep 5
        done
        [[ "$health" == healthy ]] || fail "$service did not become healthy"
    done
}

enable_perplexica "$audit_root/add.json" yes
after=""
for _ in {1..30}; do
    after="$(catalog_row)" || fail 'Library catalog unavailable after Add'
    [[ "$(python3 - "$after" <<'PY'
import json,sys
print(json.loads(sys.argv[1])["status"])
PY
)" == enabled ]] && break
    sleep 5
done
python3 - "$after" <<'PY' || fail 'Library status did not reflect healthy Perplexica'
import json,sys
row=json.loads(sys.argv[1])
assert row["status"]=="enabled" and row["library_selected"] is True, row
PY
[[ "$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" == "$api_instance" ]] \
    || fail 'Dashboard API restarted during Add'
printf 'PASS: Library Add started Perplexica and SearXNG without restarting Dashboard API\n'

curl -fsS --max-time 30 http://127.0.0.1:3004/api/providers >"$audit_root/providers.json" \
    || fail 'Perplexica providers endpoint unavailable'
python3 - "$audit_root/providers.json" "$audit_root/search-request.json" <<'PY' \
    || fail 'Perplexica did not expose the selected chat and local embedding models'
import json,sys
providers=json.load(open(sys.argv[1],encoding="utf-8"))["providers"]
chat=next(({"providerId":p["id"],"key":m["key"]}
           for p in providers for m in p.get("chatModels",[])
           if m.get("key")=="ods-acceptance-mock"),None)
embedding=next(({"providerId":p["id"],"key":m["key"]}
                for p in providers for m in p.get("embeddingModels",[])
                if m.get("key")=="Xenova/all-MiniLM-L6-v2"),None)
assert chat and embedding, [(p.get("name"),len(p.get("chatModels",[])),len(p.get("embeddingModels",[]))) for p in providers]
with open(sys.argv[2],"w",encoding="utf-8") as stream:
    json.dump({"chatModel":chat,"embeddingModel":embedding,"optimizationMode":"speed",
               "sources":["web"],"query":"What is the Linux kernel?","history":[],"stream":False},stream)
PY

requests_before="$(grep -Fc 'accept=chat' "$audit_root/mock.log" || true)"
search_code="$(curl -sS --max-time 240 -o "$audit_root/search-response.json" -w '%{http_code}' \
    -H 'Content-Type: application/json' --data-binary @"$audit_root/search-request.json" \
    http://127.0.0.1:3004/api/search)" || fail 'Perplexica research request was unreachable'
[[ "$search_code" == 200 ]] || fail "Perplexica research returned HTTP $search_code"
python3 - "$audit_root/search-response.json" <<'PY' || fail 'Perplexica research response was unusable'
import json,sys
response=json.load(open(sys.argv[1],encoding="utf-8"))
assert isinstance(response.get("message"),str) and response["message"].strip(), list(response)
assert isinstance(response.get("sources"),list) and response["sources"], list(response)
print("PASS: Perplexica accepted a research request and returned an answer")
PY
requests_after="$(grep -Fc 'accept=chat' "$audit_root/mock.log" || true)"
[[ "$requests_after" -gt "$requests_before" ]] \
    || fail 'research did not reach the authenticated ODS external model route'
for stage in 'accept=classify' 'accept=tool-call name=web_search' 'accept=writer'; do
    grep -Fq "$stage" "$audit_root/mock.log" \
        || fail "research did not reach mock stage $stage"
done
printf 'PASS: Perplexica research used the selected external model through LiteLLM\n'

sentinel="/home/vane/data/.ods-acceptance-sentinel"
docker exec ods-perplexica sh -c "printf retained > '$sentinel'" \
    || fail 'could not write retention sentinel to Perplexica data volume'
disable_code="$(curl -sS --max-time 900 -o "$audit_root/disable.json" -w '%{http_code}' \
    -X POST http://127.0.0.1:3001/api/extensions/perplexica/disable)" \
    || fail 'Perplexica Disable request was unreachable'
[[ "$disable_code" == 200 ]] || fail "Perplexica Disable returned HTTP $disable_code"
[[ -f "$INSTALL_DIR/extensions/services/perplexica/compose.yaml.disabled" ]] \
    || fail 'Perplexica fragment remained selected after Disable'
enable_perplexica "$audit_root/readd.json" no
[[ "$(docker exec ods-perplexica cat "$sentinel")" == retained ]] \
    || fail 'Perplexica data did not survive Disable and re-add'
[[ "$(docker inspect --format '{{.State.StartedAt}}|{{.RestartCount}}' ods-dashboard-api)" == "$api_instance" ]] \
    || fail 'Dashboard API restarted during re-add'
printf 'PASS: Perplexica was re-added healthy with its data retained\n'
