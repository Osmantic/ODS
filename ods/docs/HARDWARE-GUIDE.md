# ODS Hardware Sizing

> **Superseded content.** The February 2026 buying guide that used to live here
> (prices, recommended builds and per-tier user counts) no longer matched the
> model catalog and had no published measurements behind it, so it was
> retired. Its history remains in Git. This page now summarizes what the
> installer actually does with your hardware.

## How ODS sizes a machine

The installer measures GPU memory (or unified memory, or system RAM on a
CPU-only machine), assigns a hardware tier, and then picks the best installable
model and context size from its catalog for that memory. The
[hardware table in the README](../../README.md#hardware-auto-detection) lists
the current picks for common hardware. After installing, Dashboard → Models
shows every catalog model with a memory estimate, so you can choose another one.

As a rule of thumb, more GPU memory buys a larger model and a longer context:
8-16 GB of VRAM runs Qwen3.5 9B, 24-32 GB runs Qwen3.5 27B, and 48 GB or more
(or 64 GB or more of unified memory) runs Qwen3.6 35B-A3B with a 128K context.

## Disk and memory

The installer blocks on free disk space and warns about RAM for each tier, and
it also needs room for the chosen model plus 15 GB:

| Hardware (GPU memory) | Free disk | Recommended RAM |
|-----------------------|-----------|-----------------|
| NVIDIA GPU under 4 GB (tier 0) | 15 GB | 4 GB |
| CPU only, or GPU 4-11 GB (tier 1) | 30 GB | 16 GB |
| 12-19 GB (tier 2) | 50 GB | 32 GB |
| 20-39 GB (tier 3) | 80 GB | 48 GB |
| 40-89 GB (tier 4) | 150 GB | 64 GB |
| AMD Strix Halo, under 90 GB / 90 GB or more | 80 / 120 GB | 64 / 96 GB |

Use an SSD: model loading time is dominated by disk read speed.

## Drivers

- **NVIDIA:** driver 570 or newer (575 or newer for GPU-accelerated Whisper).
  Blackwell GPUs need the open kernel modules. On WSL2, install the driver in
  Windows, never inside Ubuntu.
- **AMD Strix Halo:** see the [support matrix](SUPPORT-MATRIX.md) for the
  validated configuration.

## Platform status

The [support matrix](SUPPORT-MATRIX.md) is the source of truth for what is
validated. In short: Linux with NVIDIA or AMD Strix Halo, macOS on Apple
Silicon, and Windows through Ubuntu on WSL2 are supported. AMD discrete GPUs
are detected but not yet validated, and Intel Arc needs a manual `--tier`
([Intel Arc guide](INTEL-ARC-GUIDE.md)).

## Throughput and users

ODS does not publish price, throughput or concurrent-user figures. A default
install serves one request at a time (`LLAMA_PARALLEL=1`). To serve several
people, see [Multi-User Setup](MULTI-USER-SETUP.md) and benchmark on your own
hardware (`ods benchmark`).
