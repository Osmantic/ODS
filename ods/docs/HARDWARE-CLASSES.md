# Hardware Class Mapping

ODS classifies hardware into explicit classes for predictable backend/tier defaults.

## Source of truth

- `config/hardware-classes.json`
- `scripts/classify-hardware.sh`

## Current classes

- `strix_unified` (AMD unified memory, Linux/WSL)
- `nvidia_pro` (NVIDIA discrete GPU, Linux/WSL)
- `intel_arc` (Intel Arc discrete GPU, Linux — SYCL backend via oneAPI)
- `apple_silicon` (Apple unified memory, macOS)
- `cpu_fallback` (no detected accelerator)

## Usage

```bash
scripts/classify-hardware.sh \
  --platform-id linux \
  --gpu-vendor nvidia \
  --memory-type discrete \
  --vram-mb 24576 \
  --env
```

The capability profile generator now includes:

- `hardware_class.id`
- `hardware_class.label`
