# Dream Server Support Matrix

Last updated: 2026-05-25

## What Works Today

**Linux, Windows, and macOS are fully supported. Intel Arc is experimental.**

For the release gate behind these claims, see
[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md), [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md),
and [TESTING.md](TESTING.md). The validation docs are sanitized so they can be
public without exposing private lab hostnames, LAN addresses, or paths. Support
status means the project has an intended installer/runtime path for that
platform; release evidence should still name the current run, enabled hardware
classes, and any deferred or skipped phases.

| Platform | Status | What you get today |
|----------|--------|-------------------|
| **Linux + AMD Strix Halo (ROCm)** | **Fully supported** | Complete install and runtime. Primary development platform. |
| **Linux + NVIDIA (CUDA)** | **Supported** | Complete install and runtime. Distro breadth runs in CI, private Docker containers, and private Incus VMs; GPU runtime is validated on real NVIDIA hardware. |
| **Windows (Docker Desktop + WSL2)** | **Supported** | Complete install and runtime via `.\install.ps1`. GPU auto-detection (NVIDIA/AMD). Count Windows as current release-fleet evidence only when the Windows target is enabled and produces artifacts for that candidate. |
| **macOS (Apple Silicon)** | **Supported** | Complete install and runtime via `./install.sh`. Native Metal inference + Docker services. |
| **Linux + Intel Arc (SYCL)** | **Experimental** | Installer auto-detects Arc, assigns ARC/ARC\_LITE tier, and selects `docker-compose.arc.yml`. End-to-end runtime on A770/A750. See [INTEL-ARC-GUIDE.md](INTEL-ARC-GUIDE.md). |

## Support Tiers

- `Tier A` — fully supported and actively tested in this repo
- `Tier B` — supported (works end-to-end, broader validation ongoing)
- `Tier C` — experimental or planned (installer diagnostics only, no runtime)

## Platform Matrix (detailed)

| Platform | GPU Path | Installer Tier | Notes |
|---|---|---|---|
| Linux (Ubuntu/Debian family) | NVIDIA (llama-server/CUDA) | Tier B | Validated on real high-memory multi-GPU NVIDIA hardware; broader distro matrix runs in CI, private Docker containers, and private Incus VMs |
| Linux (Strix Halo / AMD unified memory) | AMD (Lemonade/ROCm) | Tier A | Primary managed path via `docker-compose.base.yml` + `docker-compose.amd.yml`; validated on real Strix Halo hardware |
| Linux (Intel Arc A770/A750) | Intel SYCL (llama-server/oneAPI) | **Tier C** | `docker-compose.arc.yml`; builds llama.cpp from `intel/oneapi-basekit`; see [INTEL-ARC-GUIDE.md](INTEL-ARC-GUIDE.md) |
| Windows (Docker Desktop + WSL2) | NVIDIA via Docker Desktop; AMD via host Vulkan runtime | Tier B | Standalone installer (`.\install.ps1`) with GPU auto-detection, Docker orchestration, health checks, and desktop shortcuts; Windows laptop fleet target tracks Docker Desktop/WSL2 evidence |
| macOS (Apple Silicon) | Metal (native llama-server); optional existing Lemonade wrapper | Tier B | Standalone installer (`./install.sh`) with chip detection, native Metal inference, Docker services, and LaunchAgent auto-start; `--use-existing-lemonade` can route through an already-running host Lemonade service via LiteLLM |

## GPU Tier Map

| Installer Tier | Hardware | Model | VRAM | Backend |
|---|---|---|---|---|
| `NV_ULTRA` | NVIDIA 90 GB+ | Qwen3-Coder-Next | ≥ 90 GB | CUDA |
| `SH_LARGE` | AMD Strix Halo 90+ | Qwen3-Coder-Next | ≥ 90 GB (unified) | ROCm |
| `SH_COMPACT` | AMD Strix Halo < 90 GB | Qwen3 30B A3B | < 90 GB (unified) | ROCm |
| `4` | NVIDIA 40 GB+ / multi-GPU | Qwen3 30B A3B | ≥ 40 GB | CUDA |
| `3` | NVIDIA 20 GB+ | Qwen3 30B-A3B | ≥ 20 GB | CUDA |
| `ARC` | **Intel Arc ≥ 12 GB** (A770, B580) | Qwen3.5 9B | ≥ 12 GB | **SYCL** |
| `2` | NVIDIA 12 GB+ | Qwen3.5 9B | ≥ 12 GB | CUDA |
| `ARC_LITE` | **Intel Arc < 12 GB** (A750, A380) | Qwen3.5 4B | 6–11 GB | **SYCL** |
| `1` | NVIDIA 4 GB+ | Qwen3.5 9B | ≥ 4 GB | CUDA |
| `0` | CPU / < 4 GB GPU | Qwen3.5 2B | any | CPU |
| `CLOUD` | No local GPU | Claude (API) | — | LiteLLM |

## Current Truth

- **Linux, Windows, and macOS are fully supported.**
- Linux + NVIDIA is supported and validated on real high-memory NVIDIA hardware; broader distro coverage now runs through CI, private Docker containers, and private Incus VMs.
- Windows installs via `.\install.ps1` with Docker Desktop + WSL2 backend. Windows AMD local inference is host-managed and uses Vulkan today, either through legacy Lemonade Server or native `llama-server` fallback. Windows support is not inferred from Linux/macOS; treat it as release-current only when a Windows fleet target produces artifacts for that candidate.
- Windows native installer UX is Tier B (delegated via Docker Desktop + WSL2).
- macOS installs via `./install.sh` — llama-server runs natively with Metal acceleration by default, all other services in Docker. macOS can also wrap an already-running host Lemonade service with `--use-existing-lemonade`; Dream Server does not manage that Lemonade process.
- AMD runtime diagnostics are explicit: `.env` records runtime, location, selected backend, supported backends, and whether DreamServer manages the process. DreamServer supports its managed AMD Lemonade path and Linux/macOS external Lemonade SDK wrapper paths for existing Lemonade installs; see [LEMONADE-SDK-COMPAT.md](LEMONADE-SDK-COMPAT.md).
- AMD discrete GPUs beyond the documented Strix Halo path should be treated as validation-required until the repo has tier/model benchmarks for that hardware.
- **Intel Arc (SYCL) is Tier C / experimental.** The installer auto-detects and selects the correct compose overlay and tier. Runtime works on A770/A750 (Linux). ComfyUI and Whisper GPU acceleration are not yet available for Arc. See [INTEL-ARC-GUIDE.md](INTEL-ARC-GUIDE.md) for limitations.
- Release-readiness claims should cite a matching version/tag, relevant distro-lab evidence, and a real-hardware fleet receipt from [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md).
- A supported platform can have code and installer support even when it is not included in every default private release-fleet run. Release notes should cite which hardware classes actually ran, which phases passed, and which surfaces were deferred or skipped.
- Version baselines for triage are in `docs/KNOWN-GOOD-VERSIONS.md`.

## Roadmap

| Target | Milestone |
|--------|-----------|
| **Now** | Linux AMD + NVIDIA + Windows + macOS fully supported |
| **Now** | Intel Arc (SYCL) experimental — installer + runtime on A770/A750 |
| **Ongoing** | CI smoke matrix expansion for all platforms |
| **Planned** | Promote Intel Arc to Tier B after broader A770/B580 validation |
| **Planned** | Arc-accelerated Whisper STT overlay |

## Next Milestones

1. Keep the CI/container/Incus distro matrix green and add targeted full-install VM lanes where regressions justify the cost.
2. Keep Windows laptop fleet evidence current for Docker Desktop/WSL2, NVIDIA mobile GPU, and Intel hybrid-GPU behavior.
3. Expand macOS test coverage across more Apple Silicon generations and RAM tiers.
4. Validate Intel Arc B580 (Battlemage 12 GB) on the `ARC` tier.
5. Promote Intel Arc from Tier C to Tier B after A770 + B580 real-hardware validation.

## See also

- [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md) - layered CI, distro-lab, and real-hardware fleet release-readiness evidence.
- [TESTING.md](TESTING.md) - local test commands, fleet distro lab, and Incus VM runner usage.

- [LINUX-PORTABILITY.md](LINUX-PORTABILITY.md) — Linux installer edge cases, `.env` validation, extension manifests.
- [config/system-tuning/README.md](../config/system-tuning/README.md) — Performance tuning for AMD Strix Halo (GRUB, modprobe, sysctl, CPU governor settings).
