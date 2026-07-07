# Known-Good Version Baselines

Use these as minimum practical baselines for support triage.

Last updated: 2026-03-05

## macOS (Apple Silicon, Metal)

Tested configuration:

- macOS 15+ (Sequoia) / macOS 26 (Tahoe)
- Apple M4 (base, 16GB unified memory)
- Docker Desktop 4.20+ (29.2.1 tested)
- llama.cpp release: b8210

| Component | Tested version |
|-----------|---------------|
| macOS | 26.3 (25D125) |
| Chip | Apple M4 (10-core GPU) |
| RAM | 16 GB unified |
| Docker Desktop | 29.2.1 |
| llama.cpp | b8210 |
| Model | Qwen3.5-9B-Q4_K_M |
| Context | 16384 |
| Services online | 16/17 (ComfyUI not deployed — no macOS GPU backend) |

Quick checks:

```bash
uname -m           # Must be arm64
sysctl -n machdep.cpu.brand_string
system_profiler SPHardwareDataType | grep "Memory"
docker version
```

## Windows (WSL2 delegated path)

- Windows 11 23H2+ (or Windows 10 with current WSL2 support)
- WSL default version: `2`
- Docker Desktop: 4.20+ (WSL2 backend enabled)
- NVIDIA driver (if using NVIDIA): current Studio/Game Ready with WSL support

Quick checks:

```powershell
wsl --status
docker version
docker info | findstr WSL
nvidia-smi
```

WSL checks:

```bash
docker info
nvidia-smi
```

## Linux (native)

- Ubuntu 22.04+ / Debian 12+ recommended
- Docker Engine + Compose v2
- NVIDIA: modern driver + toolkit
- AMD unified memory path: current amdgpu/ROCm-compatible kernel stack

Quick checks:

```bash
docker version
docker compose version
nvidia-smi || true
```

## Standard remediation snippets

- Start Docker daemon/Desktop.
- Ensure required compose overlays exist.
- Re-run preflight and doctor:

```bash
scripts/preflight-engine.sh --help
scripts/ods-doctor.sh
```
