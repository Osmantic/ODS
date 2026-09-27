#!/usr/bin/env bash
# `ods gpu validate` on a multi-GPU NVIDIA install whose active model runs on
# one of the llama GPUs (model activation: LLAMA_ARG_SPLIT_MODE=none plus
# LLAMA_ARG_MAIN_GPU). The placement is reported as a pass; a main GPU that
# is not one of the llama GPUs fails with a fix. nvidia-smi, docker and curl
# are stubbed so the test runs on any host.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ODS_CLI="$ROOT_DIR/ods-cli"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

pass() {
    echo "[PASS] $*"
}

if ! env bash -c '(( BASH_VERSINFO[0] >= 4 ))' 2>/dev/null; then
    echo "[SKIP] ods-cli requires Bash 4+"
    exit 0
fi

fixture="$(mktemp -d)"
trap 'rm -rf "$fixture"' EXIT
install="$fixture/install"
stubs="$fixture/stubs"
mkdir -p "$install" "$stubs"
: >"$install/docker-compose.base.yml"

cat >"$stubs/nvidia-smi" <<'STUB'
#!/usr/bin/env bash
if [[ "$*" == *"--list-gpus"* ]]; then
    echo "GPU 0: NVIDIA RTX PRO 6000 Blackwell Workstation Edition (UUID: GPU-aaaa)"
    echo "GPU 1: NVIDIA RTX PRO 6000 Blackwell Workstation Edition (UUID: GPU-bbbb)"
fi
exit 0
STUB
printf '#!/usr/bin/env bash\nexit 1\n' >"$stubs/curl"
printf '#!/usr/bin/env bash\nexit 0\n' >"$stubs/docker"
chmod +x "$stubs"/*

validate() {
    # $1 = split mode, $2 = main GPU (empty: unset)
    {
        echo "GPU_BACKEND=nvidia"
        echo "GPU_COUNT=2"
        echo "LLAMA_SERVER_GPU_UUIDS=GPU-aaaa,GPU-bbbb"
        echo "LLAMA_ARG_SPLIT_MODE=$1"
        echo "LLAMA_ARG_TENSOR_SPLIT=1,1"
        [[ -z "$2" ]] || echo "LLAMA_ARG_MAIN_GPU=$2"
    } >"$install/.env"
    ODS_HOME="$install" PATH="$stubs:$PATH" "$ODS_CLI" gpu validate 2>&1 || true
}

out="$(validate none 1)"
grep -q 'Active model runs on one of 2 llama GPUs (LLAMA_ARG_SPLIT_MODE=none, LLAMA_ARG_MAIN_GPU=1)' <<<"$out" \
    || { echo "$out" >&2; fail "one-GPU placement is not reported"; }
grep -q 'Result: 2 check(s) passed, 0 failed' <<<"$out" \
    || { echo "$out" >&2; fail "one-GPU placement must pass validation"; }
pass "one-GPU placement on a two-GPU llama assignment passes"

out="$(validate none 2)"
grep -q 'LLAMA_ARG_MAIN_GPU=2 is not one of the 2 llama GPUs' <<<"$out" \
    || { echo "$out" >&2; fail "an out-of-range main GPU is not reported"; }
grep -q 'Result: 1 check(s) passed, 1 failed' <<<"$out" \
    || { echo "$out" >&2; fail "an out-of-range main GPU must fail validation"; }
pass "a main GPU outside the llama GPUs fails with a fix"

out="$(validate layer "")"
grep -q 'LLAMA_ARG_SPLIT_MODE=layer is consistent with 2 llama GPUs' <<<"$out" \
    || { echo "$out" >&2; fail "the layer split check changed"; }
grep -q 'Active model runs on one of' <<<"$out" \
    && { echo "$out" >&2; fail "a layer split must not report a one-GPU placement"; }
pass "the layer split check is unchanged"
