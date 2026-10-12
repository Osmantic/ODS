# cafe-llama service contract

Optional advanced inference runtime based on [cafe-llama.cpp](https://github.com/quimmedes/cafe-llama.cpp).

The curated Docker recipe lives in `ods/extensions/library/services/cafe-llama/`.
Runtime selection is opt-in through `CAFE_LLAMA_ENABLED=true` and a normal model
activation request. The host agent currently supports host-native Linux with
NVIDIA, or CPU with an explicitly configured compatible `CAFE_LLAMA_RELEASE_URL`.
Other runtime ownership/platform combinations fail closed.

## Runtime guarantees

- The standard `llama-server` remains the default when opt-in is disabled.
- ODS does not publish the cafe route until health, model identity, context, and
  a real visible chat completion have been verified.
- The competing runtime is stopped during handoff to avoid double VRAM use.
- A failed activation attempts to restore the previously active backend and
  its route.
- Upstream performance reports are not MEASURED evidence for local hardware.

## Files

| File | Purpose |
|------|---------|
| `manifest.yaml` | Capability and configuration contract |
| `entrypoint.sh` | Launcher contract for the upstream cafe-compatible binary |
| `README.md` | Runtime scope and guarantees |

Operator documentation: `ods/docs/CAFE-LLAMA.md`
