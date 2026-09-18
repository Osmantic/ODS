#!/usr/bin/env bash
# Exercise the real resolver and native launch assembly without running a GPU.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
INSTALL_DIR="$TMP_DIR/install"
SSD_DIR="$TMP_DIR/External SSD"
export INSTALL_DIR SSD_DIR
mkdir -p "$INSTALL_DIR/scripts" "$INSTALL_DIR/extensions/services/dashboard-api" "$INSTALL_DIR/data/models" "$INSTALL_DIR/bin" "$SSD_DIR"
cp "$ROOT_DIR/scripts/resolve-model-store.py" "$INSTALL_DIR/scripts/"
cp "$ROOT_DIR/extensions/services/dashboard-api/"{model_stores,env_values,model_mtp}.py "$INSTALL_DIR/extensions/services/dashboard-api/"
# model_stores.py loads model_mtp.py as a sibling; the fixture runtime is a Mach-O
# magic stub that cannot execute on any host, so shadow the subprocess boundary
# with a shape check (real exec coverage lives in test_model_mtp.py).
cat >> "$INSTALL_DIR/extensions/services/dashboard-api/model_mtp.py" <<'PY'


def validate_runtime_command(command):
    if not command or any(not isinstance(value, str) or '\x00' in value for value in command):
        raise ValueError("Invalid native runtime command")
PY
source "$ROOT_DIR/installers/macos/lib/native-model.sh"
read_env_value() { sed -n "s/^$2=//p" "$1" | head -1; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

write_fixture() {
    python3 - "$1" <<'PY'
import hashlib, json, os, sys
from pathlib import Path
root, ssd = Path(os.environ["INSTALL_DIR"]), Path(os.environ["SSD_DIR"])
model = ssd / "model $(literal) 'quoted'.gguf"
model.write_bytes(b"GGUF-test")
binary = ssd / "qualified runtime"
binary.write_bytes(bytes.fromhex("cffaedfe") + b"test fixture only")
binary.chmod(0o700)
profile = dict(backend=sys.argv[1], executable=str(binary), contextLength=65536, mtp=True, draftTokens=2,
               runtimeSha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
               modelSha256=hashlib.sha256(model.read_bytes()).hexdigest())
(root / "data/model-stores.json").write_text(json.dumps(dict(schemaVersion=1, stores=[dict(id="external", hostPath=str(ssd), profiles={model.name:profile})])))
(root / ".env").write_text(f"ODS_ACTIVE_MODEL_STORE=external\nGGUF_FILE={model.name}\nCTX_SIZE=32768\nLLM_MODEL=registered-model\n")
PY
}

# Source only the production launch function; OS lifecycle effects are observed
# through stubs. No real process is killed, no network or GPU is used.
eval "$(awk '/^start_native_llama\(\)/ {p=1} /^stop_native_llama\(\)/ {p=0} p' "$ROOT_DIR/installers/macos/ods-macos.sh")"
LLAMA_SERVER_BIN="$INSTALL_DIR/bin/llama-server"
LLAMA_SERVER_PID_FILE="$INSTALL_DIR/data/llama.pid"
LLAMA_SERVER_LOG="$INSTALL_DIR/data/llama.log"
CALLS="$TMP_DIR/calls"
ARGV="$TMP_DIR/argv"
read_ods_env() { ENV_ODS_MODE=local; ENV_CTX_SIZE=8192; ENV_LLAMA_ARG_SPEC_TYPE=legacy; ENV_LLAMA_ARG_CACHE_TYPE_K=f16; }
macos_configure_llm_bridge_from_env() { :; }
macos_bind_probe_host() { printf '%s' 127.0.0.1; }
get_native_llama_status() { NATIVE_LLAMA_RUNNING=true; NATIVE_LLAMA_HEALTHY=true; NATIVE_LLAMA_PID=123; }
stop_native_llama() { printf 'stop\n' >> "$CALLS"; }
exec() { printf '%s\0' "$@" > "$ARGV"; printf 'start\n' >> "$CALLS"; }
sleep() { :; }
curl() { return 0; }
ai() { :; }; ai_ok() { :; }; ai_warn() { :; }; ai_err() { :; }

write_fixture metal
start_native_llama true || fail "qualified Metal profile did not launch"
wait
[[ "$(cat "$CALLS")" == $'stop\nstart' ]] || fail "restart did not validate then replace"
python3 - "$ARGV" "$SSD_DIR" <<'PY'
import sys
from pathlib import Path
args = Path(sys.argv[1]).read_bytes().decode().split("\0")[:-1]
ssd = Path(sys.argv[2])
assert args[0] == str(ssd / "qualified runtime"), args
assert args[args.index("--model")+1] == str(ssd / "model $(literal) 'quoted'.gguf")
assert args[args.index("--ctx-size")+1] == "32768"
assert args.count("--cache-type-k") == 1 and args[args.index("--cache-type-k")+1] == "q4_0"
assert args.count("--spec-type") == 1 and args[args.index("--spec-type")+1] == "draft-mtp"
assert args[args.index("--spec-draft-type-v")+1] == "q4_0"
PY
echo "[PASS] SSD path, native runtime, context and MTP profile survive restart as literal arguments"

assert_rejected_without_stop() {
    : > "$CALLS"; rm -f "$ARGV"
    if start_native_llama true >/dev/null 2>&1; then fail "$1 accepted"; fi
    [[ ! -s "$CALLS" && ! -f "$ARGV" ]] || fail "$1 stopped the current model"
}
write_fixture vulkan
assert_rejected_without_stop "foreign Vulkan runtime"
write_fixture metal
printf 'changed' >> "$SSD_DIR/qualified runtime"
assert_rejected_without_stop "changed runtime hash"
write_fixture metal
mv "$SSD_DIR" "$TMP_DIR/disconnected"
assert_rejected_without_stop "disconnected SSD"
mv "$TMP_DIR/disconnected" "$SSD_DIR"
write_fixture metal
python3 - <<'PY'
import hashlib,json,os
from pathlib import Path
root=Path(os.environ["INSTALL_DIR"]); runtime=Path(os.environ["SSD_DIR"])/"qualified runtime"
runtime.write_bytes(b"MZ-not-a-macos-executable")
registry=root/"data/model-stores.json"; data=json.loads(registry.read_text())
next(iter(data["stores"][0]["profiles"].values()))["runtimeSha256"]=hashlib.sha256(runtime.read_bytes()).hexdigest()
registry.write_text(json.dumps(data))
PY
assert_rejected_without_stop "foreign executable with Metal label"
echo "[PASS] missing SSD, changed hash and incompatible runtime preserve working inference"

write_fixture metal
CLOUD_MODE=false; _previous_ods_mode=local
GGUF_FILE=tier.gguf; GGUF_URL=https://unused.invalid/model; LLM_MODEL=tier; MAX_CONTEXT=8192
eval "$(awk '/^    _MACOS_EXTERNAL_MODEL_READY=false/ {p=1} /^    _macos_switchboard_mode=/ {p=0} p' "$ROOT_DIR/installers/macos/install-macos.sh")"
[[ "$GGUF_FILE" == "model \$(literal) 'quoted'.gguf" && -z "$GGUF_URL" && "$LLM_MODEL" == registered-model && "$MAX_CONTEXT" == 32768 && "$_MACOS_EXTERNAL_MODEL_READY" == true ]] || fail "installer rerun replaced the active SSD contract"
echo "[PASS] installer rerun retains the selected SSD model and suppresses tier download/bootstrap"

printf 'GGUF-test' > "$INSTALL_DIR/data/models/default.gguf"
printf '#!/bin/sh\nexit 0\n' > "$LLAMA_SERVER_BIN"; chmod +x "$LLAMA_SERVER_BIN"
printf 'GGUF_FILE=default.gguf\nODS_ACTIVE_MODEL_STORE=default\n' > "$INSTALL_DIR/.env"
: > "$CALLS"
start_native_llama true || fail "default model stopped working"
wait
python3 - "$ARGV" "$INSTALL_DIR" <<'PY'
import sys
from pathlib import Path
args=Path(sys.argv[1]).read_bytes().decode().split("\0")[:-1]
assert args[0] == str(Path(sys.argv[2])/"bin/llama-server")
assert args[args.index("--model")+1] == str(Path(sys.argv[2])/"data/models/default.gguf")
assert args[args.index("--spec-type")+1] == "legacy"
PY
echo "[PASS] default installs retain the normal runtime and legacy tuning"

mv "$INSTALL_DIR/scripts/resolve-model-store.py" "$TMP_DIR/resolver.py"
assert_rejected_without_stop "registered stores without resolver"
rm "$INSTALL_DIR/data/model-stores.json"
start_native_llama true || fail "legacy default installation without registry/resolver failed"
wait
echo "[PASS] old default installations remain usable; registered stores never fall back silently"

# A shared SSD runtime must not authorize stopping another installation.
eval "$(awk '/^_macos_native_llama_cwd_is_owned\(\)/ {p=1} /^_macos_stop_install_owned_native_llama\(\)/ {p=0} p' "$ROOT_DIR/installers/macos/install-macos.sh")"
LLAMA_SERVER_BIN="$SSD_DIR/qualified runtime"
PROCESS_COMMAND="$LLAMA_SERVER_BIN --model something.gguf"
PROCESS_CWD="$TMP_DIR/other-install"
ps() { if [[ "$*" == *' comm='* ]]; then printf '%s\n' "$LLAMA_SERVER_BIN"; else printf '%s\n' "$PROCESS_COMMAND"; fi; }
lsof() { printf 'n%s\n' "$PROCESS_CWD"; }
if _macos_native_llama_pid_is_owned 123; then fail "shared runtime authorized another install"; fi
PROCESS_CWD="$INSTALL_DIR"
_macos_native_llama_pid_is_owned 123 || fail "install-owned registered runtime not recognized"
PROCESS_COMMAND="$INSTALL_DIR/bin/llama-server --model old.gguf"
_macos_native_llama_pid_is_owned 123 || fail "prior default runtime not recognized after profile selection"
echo "[PASS] shared runtime ownership remains scoped to the installation"

mv "$TMP_DIR/resolver.py" "$INSTALL_DIR/scripts/resolve-model-store.py"
write_fixture metal
(
    export ODS_INSTALL_DIR="$INSTALL_DIR"
    source "$ROOT_DIR/installers/macos/lib/constants.sh"
    source "$ROOT_DIR/installers/macos/lib/tier-map.sh"
    source "$ROOT_DIR/installers/macos/lib/env-generator.sh"
    calculate_llama_cpu_budget() { printf '4 1 8\n'; }
    SYSTEM_RAM_GB=64; DOCKER_BACKEND=docker-desktop; ODS_MODEL_SWITCHBOARD=observe
    resolve_tier_config 1
    GGUF_FILE="model \$(literal) 'quoted'.gguf"
    MAX_CONTEXT=32768
    generate_ods_env "$INSTALL_DIR" 1 true
    [[ "$(read_env_value "$INSTALL_DIR/.env" ODS_ACTIVE_MODEL_STORE)" == external ]] || fail "force regeneration lost unchanged SSD selection"
    GGUF_FILE=new-model.gguf
    generate_ods_env "$INSTALL_DIR" 1 true
    [[ "$(read_env_value "$INSTALL_DIR/.env" ODS_ACTIVE_MODEL_STORE)" == default ]] || fail "new model retained unrelated SSD selection"
) > "$TMP_DIR/env-generation.log" 2>&1 || { cat "$TMP_DIR/env-generation.log" >&2; fail "model store environment persistence"; }
echo "[PASS] environment regeneration preserves an unchanged SSD selection and resets a new model to default"
