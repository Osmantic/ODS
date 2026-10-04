# ODS Support Matrix

Last updated: 2026-10-03

## What Works Today

**Linux (NVIDIA, AMD Strix Halo), Windows (through Ubuntu on WSL2) and macOS (Apple Silicon) are supported. Only Linux + Strix Halo is Tier A; the others are Tier B. Intel Arc and AMD discrete GPUs are Tier C.**

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
| **Linux + NVIDIA (CUDA)** | **Supported** | Complete install and runtime. CI checks package-manager detection, prerequisite installation and installer syntax in 11 distro containers (not full installs); maintainers run full installs in a private distro lab, and GPU runtime is validated on real NVIDIA hardware. |
| **Windows (Docker Desktop + WSL2)** | **Supported** | `.\install.ps1` prepares WSL2, Docker Desktop and Ubuntu, then runs the Linux installer inside Ubuntu with Portal (Pixel) and without Hermes. NVIDIA GPUs run in WSL; AMD GPUs run through Lemonade (Vulkan) on Windows. Count Windows as current release-fleet evidence only when the Windows target is enabled and produces artifacts for that candidate. |
| **macOS (Apple Silicon)** | **Supported** | Complete install and runtime via `./install.sh`. Native Metal inference + Docker services. |
| **Linux + Intel Arc (SYCL)** | **Experimental** | Hardware detection does not identify Arc yet: pass `--tier ARC` or `--tier ARC_LITE` to use `docker-compose.arc.yml`. Runtime validation on A770/A750 is still pending in this repo. See [INTEL-ARC-GUIDE.md](INTEL-ARC-GUIDE.md). |
| **Linux + AMD discrete GPU** | **Validation required** | Detected and routed to the AMD overlay; no validated tier/model results yet. |

## Support Tiers

- `Tier A` — fully supported and actively tested in this repo
- `Tier B` — supported (works end-to-end, broader validation ongoing)
- `Tier C` — experimental or planned (installer diagnostics only, no runtime)

## Agent selection matrix

The ODS platform matrix above is broader than Pixel's current qualification.
Agent gating never changes whether the rest of ODS is supported.

| Host/runtime | Default agent result |
|--------------|----------------------|
| Ubuntu 24.04/26.04 or Debian 12, PID1 systemd, model route through the authenticated ODS gateway | Pixel preferred; Hermes remains available |
| WSL2 running a qualified distro with systemd, using the ODS Linux installer | Same Pixel host path as Linux; Docker Desktop alone does not install it |
| Apple Silicon macOS, using the ODS macOS installer | Native Pixel enabled by default, with Docker ingress/sandbox services; Hermes disabled while Pixel is selected |
| Qualified Pixel host using an external OpenAI-compatible or Lemonade endpoint | Pixel uses the selected upstream through the authenticated ODS LiteLLM gateway |
| Other supported Linux distributions or WSL1 | Hermes fallback |
| Legacy native Windows installer (`installers/windows/install-windows.ps1`, not used by `.\install.ps1`) | Hermes; Pixel edge/relay are disabled because no Pixel host runtime is installed |

Pixel source and its verified install bundle ship in the public ODS repository.
Installation needs neither access to `Osmantic/Pixel` nor a separate license
acknowledgement flag. See [PIXEL.md](PIXEL.md) for technical qualification and
the bundled [Pixel License for ODS](../vendor/pixel/LICENSE.md).

## Platform Matrix (detailed)

| Platform | GPU Path | Installer Tier | Notes |
|---|---|---|---|
| Linux (Ubuntu/Debian family) | NVIDIA (llama-server/CUDA) | Tier B | Validated on real high-memory multi-GPU NVIDIA hardware; CI covers package-manager and syntax checks on 11 distros, and full distro installs run in a private lab |
| Linux (Strix Halo / AMD unified memory) | AMD (Lemonade/ROCm) | Tier A | Primary managed path via `docker-compose.base.yml` + `docker-compose.amd.yml`; validated on real Strix Halo hardware |
| Linux (Intel Arc A770/A750) | Intel SYCL (llama-server/oneAPI) | **Tier C** | `docker-compose.arc.yml`; builds llama.cpp from `intel/oneapi-basekit`; see [INTEL-ARC-GUIDE.md](INTEL-ARC-GUIDE.md) |
| Windows (Docker Desktop + WSL2) | NVIDIA via Docker Desktop; AMD via Lemonade (Vulkan) on Windows | Tier B | `.\install.ps1` runs the Linux installer inside Ubuntu on WSL2 (Portal, no Hermes); the runtime lives in `~/ods` inside Ubuntu. A Windows laptop fleet target tracks Docker Desktop/WSL2 evidence |
| macOS (Apple Silicon) | Metal (native llama-server) | Tier B | Standalone installer (`./install.sh`) with chip detection, native Metal inference, Docker services, and LaunchAgent auto-start; validated on constrained and high-memory Apple Silicon lab hosts |

## GPU Tier Map

The installer assigns these tiers, then the catalog selector picks the installed
model for the measured memory (see the
[README hardware table](../../README.md#hardware-auto-detection)). The models
below are the tier-map fallback, used when the catalog is unavailable.

| Installer Tier | Hardware | Fallback model | VRAM | Backend |
|---|---|---|---|---|
| `NV_ULTRA` | NVIDIA 90 GB+ | Qwen3-Coder-Next (x86_64); Qwen3.6 35B-A3B (arm64) | ≥ 90 GB | CUDA |
| `SH_LARGE` | AMD Strix Halo 90+ | Qwen3.6 35B-A3B | ≥ 90 GB (unified) | ROCm |
| `SH_COMPACT` | AMD Strix Halo < 90 GB | Qwen3.6 35B-A3B | < 90 GB (unified) | ROCm |
| `4` | NVIDIA 40 GB+ / multi-GPU | Qwen3.6 35B-A3B | ≥ 40 GB | CUDA |
| `3` | NVIDIA 20 GB+ | Qwen3.5 27B | ≥ 20 GB | CUDA |
| `ARC` | **Intel Arc ≥ 12 GB** (A770; B580 not yet validated) | Qwen3.5 9B | ≥ 12 GB | **SYCL** |
| `2` | NVIDIA 12 GB+ | Qwen3.5 9B | ≥ 12 GB | CUDA |
| `ARC_LITE` | **Intel Arc < 12 GB** (A750, A380) | Qwen3.5 4B | 6–11 GB | **SYCL** |
| `1` | NVIDIA 4 GB+ | Qwen3.5 9B | ≥ 4 GB | CUDA |
| `0` | NVIDIA < 4 GB, or `--tier 0` (CPU-only hosts are usually tier 1) | Qwen3.5 2B | any | CPU |
| `CLOUD` | No local GPU | Claude (API) | — | LiteLLM |

## Current Truth

- **Linux (NVIDIA, AMD Strix Halo), Windows (through Ubuntu on WSL2) and macOS (Apple Silicon) are supported. Only Linux + Strix Halo is Tier A; the others are Tier B. Intel Arc and AMD discrete GPUs are Tier C.**
- Pixel uses the native macOS path on Apple Silicon or the qualified Linux/WSL2
  systemd path above. Agent eligibility is distinct from overall ODS support.
- Linux + NVIDIA is supported and validated on real high-memory NVIDIA hardware. CI checks package-manager detection, prerequisite installation and installer syntax in 11 distro containers (Ubuntu 26.04/24.04/22.04, Debian 12, Mint 21.3, Fedora 41, Rocky 9, Arch, Manjaro, CachyOS, openSUSE Tumbleweed); it does not run full installs.
- Windows installs via `.\install.ps1`, which runs the Linux installer inside Ubuntu on WSL2 with Docker Desktop. AMD local inference runs in Lemonade (Vulkan) on Windows. Windows support is not inferred from Linux/macOS; treat it as release-current only when a Windows fleet target produces artifacts for that candidate.
- The legacy native Windows installer (`installers/windows/install-windows.ps1`) is no longer used by `.\install.ps1`.
- macOS installs via `./install.sh`: native Metal inference, a native Pixel gateway and managed host helpers, plus Docker UI, ingress, sandbox and supporting services. Native Pixel does not require a separate Lima VM or Linux systemd.
- AMD runtime diagnostics are explicit: `.env` records runtime, location, selected backend, supported backends, and whether ODS manages the process. ODS supports its managed AMD Lemonade path and a Linux external Lemonade SDK wrapper path for existing Lemonade installs; see [LEMONADE-SDK-COMPAT.md](LEMONADE-SDK-COMPAT.md).
- AMD discrete GPUs beyond the documented Strix Halo path should be treated as validation-required until the repo has tier/model benchmarks for that hardware.
- **Intel Arc (SYCL) is Tier C / experimental.** Hardware detection does not identify Arc yet; pass `--tier ARC` or `--tier ARC_LITE`, which selects the Arc compose overlay. ComfyUI and Whisper GPU acceleration are not yet available for Arc. See [INTEL-ARC-GUIDE.md](INTEL-ARC-GUIDE.md) for limitations.
- Release-readiness claims should cite a matching version/tag, relevant distro-lab evidence, and a real-hardware fleet receipt from [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md).
- A supported platform can have code and installer support even when it is not included in every default private release-fleet run. Release notes should cite which hardware classes actually ran, which phases passed, and which surfaces were deferred or skipped.
- Version baselines for triage are in `docs/KNOWN-GOOD-VERSIONS.md`.

## Roadmap

| Target | Milestone |
|--------|-----------|
| **Now** | Linux (NVIDIA, Strix Halo), Windows (WSL2) and macOS supported; Strix Halo is Tier A |
| **Now** | Intel Arc (SYCL) experimental: manual `--tier`, runtime validation pending |
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
