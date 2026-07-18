# ODS Android Lite (Termux) — Experimental

**Status: Tier C / experimental. Termux-only. CPU-only. Not "Android support."**

Android Lite is a minimal, native profile of ODS for Android phones: a pinned
llama.cpp CPU runtime, a verified model download, and a small `ods-mobile`
CLI. It is deliberately **not** the ODS stack — there is no Docker, no
n8n, no Open WebUI, no dashboard, no extension system, and no host-agent on
Android. It is a local runtime plus a thin CLI.

Nothing in this document claims phone performance. No throughput number for
this profile may be published until it is measured on a real device with full
provenance (see [Benchmark protocol](#benchmark-protocol)).

## What you get

- Upstream llama.cpp (pinned tag, see `installers/mobile/lib/constants.sh`)
  built on-device for CPU
- A model-agnostic mobile catalog (`config/mobile-models.json`) with pinned
  sha256 checksums — default model: **Bonsai 8B Q1_0** (~1.16 GB)
- `ods-mobile` CLI: `status`, `chat`, `serve` (OpenAI-compatible API on
  `127.0.0.1:8080`), `bench`, `models`

The default model is a default, not the architecture: any llama.cpp-compatible
GGUF can be added to the catalog with a pinned checksum.

## Requirements

- Android phone, **aarch64**, with [Termux](https://termux.dev)
  (F-Droid or GitHub build recommended)
- **12 GB+ RAM recommended** for the default 8B model; 6 GB+ works with the
  `qwen3.5-2b-q4` fallback
- ~8 GB free storage (build tree + binaries + one model)
- Patience: llama.cpp compiles on the phone. Keep the screen on or run
  `termux-wake-lock` first.

Sensible first target devices: Snapdragon 8 Gen 3 / 8 Elite class, 12 GB+ RAM.
Fold-style phones need thermal testing before any sustained-use claims.

## Install

Inside Termux:

```bash
pkg install -y git
git clone --depth 1 https://github.com/Osmantic/ODS.git
cd ODS/ods
bash installers/mobile/install-mobile.sh
```

Useful variants:

```bash
bash installers/mobile/install-mobile.sh --skip-model     # runtime + CLI only
bash installers/mobile/install-mobile.sh --model qwen3.5-2b-q4
bash installers/mobile/install-mobile.sh --dry-run        # print the plan, change nothing
bash installers/mobile/install-mobile.sh --rebuild        # force fresh llama.cpp build
```

`--skip-model` leaves a fully working runtime; pull models later with
`ods-mobile models pull <id>`.

Everything lives under `~/.ods-mobile`:

```
~/.ods-mobile/
  env         # config (model, context, host/port, pinned llama.cpp tag+commit)
  bin/        # llama-cli, llama-server, llama-bench (self-contained)
  models/     # verified GGUFs
  config/     # installed copy of mobile-models.json
  lib/        # shared model download/verify code
  llama.cpp/  # source checkout — deletable after install to reclaim space
  logs/
```

Uninstall = delete `~/.ods-mobile` and `$PREFIX/bin/ods-mobile`.

## Usage

```bash
ods-mobile status                 # runtime, models, config, RAM/disk
ods-mobile chat                   # interactive chat with the default model
ods-mobile chat --ctx 2048        # smaller context = less memory
ods-mobile serve                  # OpenAI-compatible API on 127.0.0.1:8080
ods-mobile serve --port 8081 --ctx 8192
ods-mobile bench                  # llama-bench + provenance template
ods-mobile models list
ods-mobile models pull qwen3.5-2b-q4
ods-mobile models use qwen3.5-2b-q4
```

### Context is the memory control

`--ctx` (default **4096**) is the primary knob on phones. If Android kills the
process, lower it. If you have RAM headroom and need longer conversations,
raise it up to the model's `context_max`. Generation sampling defaults (for
Bonsai: temp 0.5, top-k 20, top-p 0.9) come from the catalog per model, not
from code.

`serve` binds to `127.0.0.1` by default. Binding wider exposes an
unauthenticated LLM API to your network — the CLI warns if you do.

## Model catalog and checksums

`config/mobile-models.json` pins a real sha256 and byte size for every model,
with a provenance note recording when and how it was verified. Downloads
resume on flaky connections and are verified before being kept; a checksum
mismatch deletes the download and fails loudly. **Never bypass a checksum
failure** — if upstream re-published a file, the catalog entry must be
re-verified and updated instead.

Adding a model: add an entry with `id`, `gguf_file`, `gguf_url`, a
freshly-verified `gguf_sha256` (64-char hex — CI rejects placeholders),
`size_bytes`, `min_ram_gb`, `context_default`/`context_max`, and either
`gen_defaults` or `null` to use llama.cpp defaults.

## Benchmark protocol

Any published tok/s number for Android Lite **must** include all of:

| Field | Example |
|---|---|
| Device model | (exact retail model) |
| SoC + RAM | Snapdragon 8 Elite, 12 GB |
| Backend | CPU (llama.cpp, no GPU offload) |
| llama.cpp tag + commit | from `ods-mobile status` |
| Model file + sha256 | from catalog |
| Context depth | 4096 (`ods-mobile bench --ctx N` → `llama-bench -d N`; use `--ctx 0` for the community-standard zero-depth run) |
| Thermal state | cold vs soaked; screen on/off; charging |
| Run length | n runs × tokens per run |

`ods-mobile bench` prints this template before running `llama-bench`.
A number without every field is not a result. Third-party published figures
(e.g. community numbers for other devices/backends) are not ODS validation
evidence and must not be presented as such.

## Real-device validation checklist (pre-Tier-B)

- [ ] Fresh Termux → full install completes on a clean device
- [ ] Reinstall is idempotent (`install-mobile.sh` again → skips build, verifies model)
- [ ] `ods-mobile chat` holds a multi-turn conversation
- [ ] `ods-mobile serve` + `curl 127.0.0.1:8080/v1/models` from Termux
- [ ] `ods-mobile bench` with full provenance recorded, cold and thermally soaked
- [ ] `--skip-model` install then `models pull` works
- [ ] Behavior under phantom process killer documented (with/without wake lock)
- [ ] Battery/thermal notes for a 30-minute serve session

## Troubleshooting

- **Build or server dies in the background** — Android 12+ kills "phantom"
  child processes aggressively. Run `termux-wake-lock` before long operations,
  keep Termux in the foreground, and consider disabling battery optimization
  for Termux.
- **Model load or generation OOM-killed** — lower `--ctx`, close other apps,
  or switch to the low-RAM fallback: `ods-mobile models use qwen3.5-2b-q4`
  (pull it first).
- **Out of disk** — after a successful install the binaries are
  self-contained; delete `~/.ods-mobile/llama.cpp` to reclaim the build tree.
- **Checksum mismatch on download** — retry once (network corruption); if it
  persists, upstream changed the file. Do not bypass — see the catalog policy
  above.

## Non-goals (this iteration) and roadmap

Out of scope today: OpenCL/Adreno and Vulkan acceleration, iOS, pairing with a
home ODS server, any part of the Docker stack, NNTrainer/FSU-style large-MoE
phone modes, and Termux:Boot autostart.

Planned order: (1) measured CPU baseline on real devices → (2) OpenCL/Adreno
evaluation with same-device A/B numbers → (3) Vulkan only if measured wins →
(4) optional pairing with a home ODS instance. GPU work lands only with
same-device, same-model, full-provenance comparisons against the CPU baseline.
