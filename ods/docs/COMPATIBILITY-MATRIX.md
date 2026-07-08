# ODS Compatibility Matrix

ODS is built to run on a wide range of hardware: from high-end servers to older laptops and desktops (e.g. a 2015 PC or an older MacBook). This document summarizes **what runs where** and how we support **broad compatibility**.

## Goals

1. **Rock-solid installs** — Same install path works reliably across supported combinations.
2. **Broad compatibility** — From a 12k+ server to a consumer machine or older hardware.
3. **App integration library** — Extensions that work for the ODS version you are on (see [EXTENSIONS.md](EXTENSIONS.md) and [extensions/CATALOG.md](../extensions/CATALOG.md)).

---

## Platform overview

| Platform        | Installer        | GPU / inference              | Status   |
|----------------|------------------|------------------------------|----------|
| Linux (native) | `./install.sh`   | NVIDIA, AMD, CPU-only        | Primary  |
| macOS (Apple Silicon) | `./install.sh` | Metal (native)               | Supported |
| Windows        | `.\install.ps1`  | Docker Desktop + WSL2, NVIDIA/AMD | Supported |
| WSL2 (Linux in Windows) | `./install.sh` (Linux) | Depends on host GPU passthrough | Supported |

---

## Linux: distros and package managers

The Linux installer detects the distro via `/etc/os-release` and chooses the right package manager and commands.

| Distro family   | Package manager | Typical distros                    | Notes |
|-----------------|----------------|------------------------------------|-------|
| Debian/Ubuntu   | apt            | Ubuntu 22.04/24.04, Debian 11/12   | Most tested; Docker install via get.docker.com or distro packages. |
| Fedora / RHEL   | dnf            | Fedora 38/39/40/41                | Well supported. |
| Arch            | pacman         | Arch Linux, CachyOS               | Supported; ensure curl and optional jq/rsync. |
| openSUSE        | zypper         | openSUSE Tumbleweed, Leap        | Supported. |
| Other           | (detected)     | Derivatives of above               | Installer falls back to apt-style messages when unknown. |

**Minimum versions:** We test on current LTS and recent stable releases. Older versions may work but are not guaranteed; see [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) for tier definitions.

---

## Linux: GPU and CPU-only

| Backend   | Use case              | Requirements                          | Compose overlay           |
|-----------|------------------------|----------------------------------------|----------------------------|
| **NVIDIA**| CUDA inference         | NVIDIA GPU, drivers, nvidia-container-toolkit | docker-compose.nvidia.yml |
| **AMD**   | ROCm (e.g. Strix Halo) | AMD GPU, ROCm stack                   | docker-compose.amd.yml    |
| **Apple** | Metal (macOS only)     | Apple Silicon, macOS 13+              | Native binary + Docker   |
| **CPU**   | No GPU                 | Any x86_64/arm64 Linux                | docker-compose.base.yml + CPU backend |

**CPU-only path:** Supported. The installer detects no GPU (or no supported GPU), assigns a tier, and selects the CPU backend. Performance is lower but install and run work. Good for older PCs, headless servers, or testing.

**Old PCs (e.g. 2015):** If the machine runs a supported Linux distro and can run Docker, the installer can run. Use a lower tier and smaller model; see tier mapping in [HARDWARE-GUIDE.md](HARDWARE-GUIDE.md) and [installers/lib/tier-map.sh](../installers/lib/tier-map.sh).

---

## macOS

| Variant        | Status   | Notes |
|----------------|----------|-------|
| Apple Silicon (M1/M2/M3/M4) | Supported | Metal-native LLM; Docker for other services. |
| Intel Mac      | Not supported | Current macOS path assumes Apple Silicon and Metal. |

**Minimum:** macOS 13 (Ventura) for Metal 3. Docker Desktop must be installed and running.

---

## Windows

| Setup                    | Status   | Notes |
|-------------------------|----------|-------|
| Docker Desktop + WSL2   | Supported | Primary path; GPU via Docker Desktop WSL2 backend. |
| NVIDIA GPU              | Supported | When WSL2 GPU passthrough is configured. |
| AMD GPU                 | Supported | When Docker Desktop and drivers are set up. |
| Native Windows (no WSL2)| Not supported | Containers run in WSL2. |

See [WINDOWS-QUICKSTART.md](WINDOWS-QUICKSTART.md) and [WSL2-GPU-PASSTHROUGH.md](WSL2-GPU-PASSTHROUGH.md) for setup.

---

## RAM and disk

| Resource | Minimum (practical) | Recommended | Notes |
|----------|---------------------|-------------|-------|
| RAM      | 8 GB                | 16 GB+      | Tier and model selection adapt; low RAM triggers warnings in phase 04. |
| Disk     | 30 GB free          | 50 GB+      | For base images + model + optional services. |
| GPU VRAM | 0 (CPU) or 6 GB+    | 8 GB+       | More VRAM allows larger models and ComfyUI. |

The installer checks RAM and disk in phase 04 and can warn or block depending on configuration. See [installers/phases/04-requirements.sh](../installers/phases/04-requirements.sh).

---

## Docker and kernel

| Requirement     | Linux              | macOS / Windows     |
|-----------------|--------------------|----------------------|
| Docker          | 20.10+ recommended | Docker Desktop (current) |
| Docker Compose | v2 (compose in Docker CLI) | Bundled with Docker Desktop |
| Kernel (Linux) | 5.x+ typical       | N/A                  |
| NVIDIA (Linux) | nvidia-container-toolkit | N/A (Windows: WSL2 + host driver) |

The installer can install Docker on Linux (phase 05) or prompt you to install it. On macOS and Windows, install Docker Desktop first.

---

## Extensions and version compatibility

Extensions declare compatibility with ODS versions via `compatibility.ods_min` (and optional `ods_max`) in their manifest. The script `scripts/validate-manifests.sh` and `ods config validate` check that enabled extensions are compatible with the current core version.

- **All bundled extensions** in `extensions/services/` declare `ods_min: "2.0.0"` for the current release.
- Adding a new extension: set `compatibility.ods_min` so the validator can warn on version mismatch. See [EXTENSIONS.md](EXTENSIONS.md) and [extensions/schema/README.md](../extensions/schema/README.md).

---

## Summary table: “Can I run it?”

| Scenario                    | Supported | Notes |
|----------------------------|-----------|-------|
| Linux Ubuntu 24.04 + NVIDIA | Yes       | Primary path. |
| Linux Fedora + AMD GPU     | Yes       | ROCm path. |
| Linux Debian + no GPU      | Yes       | CPU-only; lower tier. |
| Linux Arch + NVIDIA        | Yes       | Use pacman for optional tools. |
| Old PC (2015) + Linux + Docker | Possible | CPU-only or old GPU; use small model, check RAM. |
| macOS Apple Silicon        | Yes       | Metal + Docker Desktop. |
| macOS Intel                | No        | Not in current path. |
| Windows + Docker Desktop + WSL2 | Yes | GPU if configured. |
| Windows without WSL2       | No        | Containers need WSL2. |

For the latest tier and platform status, see [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md). For install steps, see [QUICKSTART.md](../QUICKSTART.md).
