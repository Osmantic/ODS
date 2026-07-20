#!/bin/sh
# Lemonade config-sync wrapper.
#
# Lemonade reads its settings from /root/.cache/lemonade/config.json. That file is
# written on Lemonade's first start from env — but on subsequent starts Lemonade
# ignores the env vars and trusts whatever is already in config.json. The cached
# config wins, so changing a value in docker-compose has no effect on an existing
# container. This wrapper reconciles config.json with env before Lemonade boots.
#
# Synced keys:
#
#   ctx_size          <- LEMONADE_CTX_SIZE
#       Qwen3.6-35B-A3B (native 256k context) was being served at 64k on strix-halo,
#       a 4x under-utilization with 7% of the memory budget in use.
#
#   llamacpp.rocm_bin <- LEMONADE_LLAMACPP_ROCM_BIN
#       Without this, rocm_bin stays "builtin" and Lemonade tries to DOWNLOAD a
#       llama-server matching the gfx arch it detects. Its arch detection maps GPU
#       marketing names to gfx targets and does not know the Radeon AI PRO R9700, so
#       on this host it resolved the *iGPU* and fetched
#           llama-b1231-ubuntu-rocm-gfx1036-x64.zip  -> HTTP 404
#       and inference failed to start. Pointing rocm_bin at the binary we build in
#       Dockerfile.amd (correct gfx target, self-contained ROCm stack) avoids the
#       download and the arch guess entirely.
#
#   llamacpp.backend  <- LEMONADE_LLAMACPP
#       "auto" makes Lemonade probe for ROCm; the same name-based arch detection
#       fails on unrecognised cards and it silently falls back to the Vulkan build
#       (radv). Forcing "rocm" keeps it on the ROCm path.
#
# Idempotent: only writes when a value actually differs. If config.json does not yet
# exist, it is seeded from Lemonade's shipped defaults so that even a FIRST start on
# a fresh volume gets the right binary — otherwise the first run would attempt the
# bad download before we ever got a chance to fix the file.
set -e

# Defensive: ROCm's HSA runtime checks whether HSA_OVERRIDE_GFX_VERSION is *defined*,
# not whether it is non-empty. Defined-but-empty makes it try to parse "" as a gfx
# version, fail, and enumerate ZERO devices — inference then silently drops to CPU or
# Vulkan. Compose is supposed to omit the variable entirely (see docker-compose.amd.yml),
# but scrub it here too: a single stray "VAR=${VAR:-}" anywhere in the compose merge
# chain is enough to reintroduce it, and the failure mode is quiet.
if [ -z "${HSA_OVERRIDE_GFX_VERSION:-}" ]; then unset HSA_OVERRIDE_GFX_VERSION || true; fi
if [ -z "${HSA_XNACK:-}" ]; then unset HSA_XNACK || true; fi

CONFIG=/root/.cache/lemonade/config.json
DEFAULTS=/opt/lemonade/resources/defaults.json

if [ ! -f "$CONFIG" ] && [ -f "$DEFAULTS" ]; then
    mkdir -p "$(dirname "$CONFIG")"
    cp "$DEFAULTS" "$CONFIG"
    echo "[lemonade-entrypoint] seeded config.json from defaults.json" >&2
fi

if [ -f "$CONFIG" ]; then
    python3 - <<'PYEOF'
import json, os, sys

path = "/root/.cache/lemonade/config.json"
try:
    with open(path) as f:
        cfg = json.load(f)
except (OSError, ValueError) as e:
    print(f"[lemonade-entrypoint] cannot read config.json ({e}); leaving it alone", flush=True)
    sys.exit(0)

changed = []

ctx = os.environ.get("LEMONADE_CTX_SIZE", "")
if ctx.isdigit() and int(ctx) > 0 and cfg.get("ctx_size") != int(ctx):
    changed.append(f"ctx_size: {cfg.get('ctx_size')} -> {int(ctx)}")
    cfg["ctx_size"] = int(ctx)

for env_var, key in (("LEMONADE_LLAMACPP_ROCM_BIN", "rocm_bin"),
                     ("LEMONADE_LLAMACPP", "backend")):
    want = os.environ.get(env_var, "")
    if not want:
        continue
    section = cfg.setdefault("llamacpp", {})
    if section.get(key) != want:
        changed.append(f"llamacpp.{key}: {section.get(key)} -> {want}")
        section[key] = want

if not changed:
    sys.exit(0)

for line in changed:
    print(f"[lemonade-entrypoint] updating {line}", flush=True)

with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF
fi

exec /opt/lemonade/lemonade-server "$@"
