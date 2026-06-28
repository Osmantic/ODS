# Intel Arc GPU Guide

*Last updated: 2026-03-17*

ODS supports Intel Arc discrete GPUs via the **llama.cpp SYCL backend**
(`docker-compose.arc.yml`). This guide covers supported hardware, driver setup,
known limitations, and performance expectations.

---

## Supported Hardware

### Tier: ARC  (≥ 12 GB VRAM)

| GPU | VRAM | Estimated tok/s | Concurrent users | Model |
|-----|------|----------------|-----------------|-------|
| Arc A770 | 16 GB | ~35 | 3–5 | Qwen3.5 9B Q4\_K\_M |
| Arc B580 | 12 GB | ~30 | 2–4 | Qwen3.5 9B Q4\_K\_M |

### Tier: ARC\_LITE  (< 12 GB VRAM)

| GPU | VRAM | Estimated tok/s | Concurrent users | Model |
|-----|------|----------------|-----------------|-------|
| Arc A750 | 8 GB | ~20 | 1–2 | Qwen3.5 4B Q4\_K\_M |
| Arc A380 | 6 GB | ~15 | 1 | Qwen3.5 4B Q4\_K\_M |
| Arc A310 | 4 GB | ~10 | 1 | Qwen3.5 4B Q4\_K\_M (tight) |

> **A310 note:** 4 GB VRAM is borderline for Qwen3.5 4B Q4\_K\_M (~3.3 GB).
> The model will load but leaves little headroom for KV cache.
> Consider `--ctx-size 4096` (set `CTX_SIZE=4096` in `.env`) to reduce pressure.

### Future / untested

Intel Arc B-series (Battlemage) cards ≥ 12 GB will automatically map to the
`ARC` tier. Cards < 12 GB will map to `ARC_LITE`.
Battlemage introduced `0x7d` PCI device IDs; `detect_gpu()` in
`installers/lib/detection.sh` may need an update when those cards become
more widely available.

---

## Host Driver Setup (Ubuntu / Debian)

### 1 — Add Intel GPU repository

```bash
# Import Intel GPG key and repo
wget -qO - https://repositories.intel.com/gpu/intel-graphics.key \
  | sudo gpg --dearmor -o /usr/share/keyrings/intel-graphics.gpg

echo "deb [arch=amd64 signed-by=/usr/share/keyrings/intel-graphics.gpg] \
  https://repositories.intel.com/gpu/ubuntu jammy unified" \
  | sudo tee /etc/apt/sources.list.d/intel-gpu-jammy.list

sudo apt update
```

### 2 — Install kernel and user-mode drivers

```bash
# Kernel module + firmware (i915 / xe)
sudo apt install -y linux-headers-$(uname -r) \
    intel-i915-dkms intel-fw-gpu

# Level Zero runtime (required for SYCL)
sudo apt install -y intel-level-zero-gpu level-zero

# OpenCL runtime (required for llama.cpp OpenCL fallback)
sudo apt install -y intel-opencl-icd

# Monitoring tools (optional but recommended)
sudo apt install -y intel-gpu-tools clinfo
```

### 3 — Add user to GPU groups

```bash
sudo usermod -aG video,render $USER
# Re-login (or newgrp render) for the change to take effect
```

### 4 — Verify

```bash
# Should list Intel Arc as an OpenCL device
clinfo | grep -A3 "Device Name"

# Should show Level Zero GPU
ze_info 2>/dev/null | grep -i "device name" || \
    ldconfig -p | grep libze_loader

# Should show render node
ls -la /dev/dri/renderD*

# Live GPU usage (Ctrl+C to exit)
sudo intel_gpu_top
```

---

## Installation

The ODS installer auto-detects Intel Arc and selects the correct tier:

```bash
# Automatic (recommended)
./install.sh

# Force a specific tier manually
./install.sh --tier ARC
./install.sh --tier ARC_LITE
```

### What the installer does for Intel Arc

1. **Phase 01 (preflight)** — checks disk space (≥ 20 GB for model download)
2. **Phase 02 (detection)** — confirms Arc via `lspci` + sysfs, validates Level Zero,
   `/dev/dri`, `intel_gpu_top`, and `video`/`render` group membership
3. **Phase 05 (docker)** — validates `docker-compose.arc.yml` syntax
4. **Phase 06 (directories)** — writes `.env` with `GPU_BACKEND=sycl`,
   `N_GPU_LAYERS=99`, `VIDEO_GID`, `RENDER_GID`, and Intel oneAPI env vars
5. **Phase 07 (devtools)** — installs OpenCode and CLI tooling
6. **Phase 08 (launch)** — runs `docker compose -f docker-compose.base.yml -f docker-compose.arc.yml up -d`

---

## Docker Compose Overlay

ODS provides two Intel Arc overlays:

| File | Image | When to use |
|------|-------|-------------|
| `docker-compose.arc.yml` | Built locally from `intel/oneapi-basekit` | Default. Requires `docker compose up --build` on first run (~10–20 min). |
| `docker-compose.intel.yml` | Pre-built `ghcr.io/ggml-org/llama.cpp:server-intel-*` | Quick start — no build time. Set `LLAMA_ARC_IMAGE=<tag>` in `.env`. |

### Manual compose start

```bash
# Build and start (first time ~10–20 min build)
docker compose -f docker-compose.base.yml -f docker-compose.arc.yml up -d --build

# Subsequent starts (no rebuild)
docker compose -f docker-compose.base.yml -f docker-compose.arc.yml up -d

# Skip local build — use a pre-built image
LLAMA_ARC_IMAGE=ghcr.io/ggml-org/llama.cpp:server-intel-b8248 \
  docker compose -f docker-compose.base.yml -f docker-compose.arc.yml up -d
```

### Key `.env` variables for Arc

```dotenv
GPU_BACKEND=sycl
N_GPU_LAYERS=99
VIDEO_GID=44          # auto-set by installer
RENDER_GID=992        # auto-set by installer
ONEAPI_DEVICE_SELECTOR=level_zero:gpu
SYCL_CACHE_PERSISTENT=1
ZES_ENABLE_SYSMAN=1
CTX_SIZE=32768        # ARC tier default
```

---

## Known Limitations vs NVIDIA / AMD

| Feature | NVIDIA (CUDA) | AMD (ROCm) | Intel Arc (SYCL) |
|---------|--------------|-----------|-----------------|
| Installer maturity | Tier B | Tier A | **Tier C (experimental)** |
| llama.cpp backend | CUDA (native) | HIP/ROCm (native) | SYCL (via oneAPI) |
| SYCL kernel cache | — | — | First-run JIT compile per container start (~30 s). Eliminated after first run with `SYCL_CACHE_PERSISTENT=1`. |
| Multi-GPU | ✅ (native) | ✅ (ROCm multi) | ❌ Not supported. SYCL backend targets a single Arc GPU. |
| ComfyUI (image gen) | ✅ CUDA overlay | ✅ ROCm overlay | ⚠️ No dedicated overlay. ComfyUI will use CPU fallback. |
| Whisper STT | ✅ CUDA overlay | ✅ ROCm overlay | ⚠️ Runs on CPU (no Arc-accelerated Whisper image). |
| Flash attention | ✅ | ✅ | ❌ llama.cpp SYCL does not yet implement Flash Attention. |
| FP16 compute | ✅ Full | ✅ Full | ✅ Enabled (`GGML_SYCL_F16=ON`) — Arc FP16 throughput is competitive at this model size. |
| Docker image size | ~6 GB | ~8 GB | **~15 GB** (oneAPI Base Toolkit is large). |
| First-run build time | Pull only | Pull only | **~10–20 min** (compiles llama.cpp from source). |
| Windows support | ✅ WSL2 | ✅ WSL2 | ⚠️ Experimental. Arc drivers for WSL2 are less mature than NVIDIA's. |

---

## Performance Expectations

Performance figures below are measured with Qwen3 models at Q4\_K\_M quantisation,
`--n-gpu-layers 99` (all layers on GPU), `--ctx-size 16384`.

| GPU | Model | Prompt tok/s | Generate tok/s | Notes |
|-----|-------|------------|----------------|-------|
| Arc A770 (16 GB) | Qwen3.5 9B Q4\_K\_M | ~120 | ~35 | Comfortable fit; KV cache well within VRAM |
| Arc A750 (8 GB) | Qwen3.5 4B Q4\_K\_M | ~90 | ~20 | Model fits; limit `CTX_SIZE` to ≤ 16384 |
| Arc A380 (6 GB) | Qwen3.5 4B Q4\_K\_M | ~70 | ~15 | Tight. Set `CTX_SIZE=8192` for safety |

### Comparison to equivalent NVIDIA tiers

| Intel Arc | Comparable NVIDIA | VRAM | Generate tok/s delta |
|-----------|------------------|------|---------------------|
| A770 (ARC) | RTX 3060 12 GB (T1) | 16 vs 12 GB | Arc ~+5 tok/s on 8B (more VRAM headroom) |
| A750 (ARC\_LITE) | RTX 3060 12 GB (T1) | 8 vs 12 GB | Arc ~-10 tok/s (less VRAM, smaller model) |

> Intel Arc SYCL throughput is broadly similar to an equivalent NVIDIA card at
> the same VRAM tier. Arc's primary advantage is **value** (A770 16 GB retails
> at ~$250–300) rather than raw throughput.

---

## Troubleshooting

### `llama-server` exits immediately with SYCL error

```
SYCL error: code 6, ZE_RESULT_ERROR_DEVICE_LOST
```

**Cause:** Level Zero cannot enumerate a GPU device.
**Fix:**
```bash
# Verify host driver
clinfo | grep "Device Name"
# If empty:
sudo apt install intel-level-zero-gpu level-zero
# Then restart the container
docker compose restart llama-server
```

---

### Slow first inference after container start

**Cause:** SYCL kernel JIT compilation on first call (~20–60 s).
**Fix:** Ensure `SYCL_CACHE_PERSISTENT=1` is set in `.env` (the installer sets
this automatically). Subsequent runs use the compiled kernel cache and start
in < 5 s.

---

### `/dev/dri` not found inside container

```
Error opening /dev/dri/renderD128: Permission denied
```

**Cause:** User not in `render` group, or Docker socket not passed through.
**Fix:**
```bash
sudo usermod -aG render $USER
# Re-login, then:
docker compose -f docker-compose.base.yml -f docker-compose.arc.yml up -d
```

---

### Container fails to start on WSL2

Intel Arc drivers on WSL2 are less mature than NVIDIA's. If the Arc GPU is not
visible inside WSL2:

1. Update Windows to the latest version (22H2+).
2. Install the latest Intel Graphics driver from [intel.com/arc-drivers](https://www.intel.com/content/www/us/en/products/docs/discrete-gpus/arc/software.html).
3. Verify the GPU is visible: `wsl -- ls /dev/dri`
4. If still missing, fall back to CPU mode: `./install.sh --tier 1` (runs inference on CPU, no GPU passthrough).

---

### `intel_gpu_top` shows 0% GPU engine utilisation during inference

This is a known display quirk when the compute engine is used heavily — `intel_gpu_top`
sometimes under-reports Arc engine utilisation in older versions of `intel-gpu-tools`.
Verify the model is actually running on GPU by checking VRAM:
```bash
# Should show non-zero VRAM used
sudo intel_gpu_top -l 1 | grep -i mem
```

---

## Related Docs

- [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) — platform support tiers
- [HARDWARE-GUIDE.md](HARDWARE-GUIDE.md) — GPU buying guide and tier overview
- [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — general installer troubleshooting
- [`docker-compose.arc.yml`](../docker-compose.arc.yml) — Intel Arc compose overlay
- [`images/llama-sycl/Dockerfile`](../images/llama-sycl/Dockerfile) — SYCL build image
