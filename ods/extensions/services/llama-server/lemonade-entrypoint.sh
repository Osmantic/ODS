#!/bin/sh
# Lemonade context-size sync wrapper.
#
# Lemonade reads `ctx_size` from /root/.cache/lemonade/config.json. That
# file is written on Lemonade's first start using LEMONADE_CTX_SIZE from
# env — but on subsequent starts Lemonade ignores the env var and trusts
# whatever's already in config.json. So bumping LEMONADE_CTX_SIZE in
# docker-compose has no effect on an existing container; the cached
# config wins.
#
# That meant Qwen3.6-35B-A3B (native 256k context) was being served at
# 64k on strix-halo, a 4× under-utilization with 7% of the memory budget
# in use. Fixed by ensuring config.json's ctx_size always matches env
# before Lemonade boots.
#
# Idempotent — does nothing if config.json is absent (first start) or
# already has the right value.
set -e

CONFIG=/root/.cache/lemonade/config.json
if [ -f "$CONFIG" ] && [ -n "${LEMONADE_CTX_SIZE:-}" ]; then
    python3 - <<PYEOF
import json, sys, os, stat, tempfile
p = os.path.realpath("$CONFIG")
want = int(os.environ.get("LEMONADE_CTX_SIZE", "0"))
if want <= 0:
    sys.exit(0)
with open(p) as f:
    cfg = json.load(f)
current = cfg.get("ctx_size")
if current == want:
    sys.exit(0)
print(f"[lemonade-entrypoint] updating ctx_size: {current} -> {want}", flush=True)
cfg["ctx_size"] = want
# Keep the last complete cache until the replacement has been fully written.
# Resolving the path above preserves an operator's existing config symlink.
fd, temporary = tempfile.mkstemp(prefix=".lemonade-config-", suffix=".tmp", dir=os.path.dirname(p))
try:
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=2)
        f.flush()
        os.fchmod(f.fileno(), stat.S_IMODE(os.stat(p).st_mode))
        os.fsync(f.fileno())
    os.replace(temporary, p)
finally:
    try:
        os.unlink(temporary)
    except FileNotFoundError:
        pass
PYEOF
fi

exec /opt/lemonade/lemonade-server "$@"
