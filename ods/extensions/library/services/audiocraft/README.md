# AudioCraft

Meta's generative audio toolkit includes MusicGen for text-to-music and
AudioGen for text-to-sound generation. **The distributed model weights are
licensed for noncommercial use under CC BY-NC 4.0.** This extension does not
grant commercial clearance for model use or generated audio.

## Requirements

- **GPU:** NVIDIA (min 6 GB VRAM)
- **Dependencies:** None

## Apple Silicon (M1/M2/M3) note

This extension is configured `platform: linux/amd64` because some of its Python dependencies don't have native ARM64 wheels. On Apple Silicon, Docker Desktop runs it under QEMU x86_64 emulation — expect noticeably slower builds (typically 5–10x) and reduced runtime CPU performance (typically 2–5x) compared to native ARM64 hosts. Functional but not recommended for active iterative work on Apple Silicon.

## Enable / Disable

```bash
ods enable audiocraft
ods disable audiocraft
```

Your data is preserved when disabling. To re-enable later: `ods enable audiocraft`

## Access

- **URL:** `http://localhost:7863`

## First-Time Setup

1. Enable the service: `ods enable audiocraft`
2. Open `http://localhost:7863`
3. Use the MusicGen tab to generate music from text descriptions
4. Use the AudioGen tab to generate sound effects

Models are downloaded automatically on first use.

## Known Issues

AudioCraft [code is MIT-licensed](https://github.com/facebookresearch/audiocraft/blob/main/LICENSE).
Its [model weights use CC BY-NC 4.0](https://github.com/facebookresearch/audiocraft/blob/main/LICENSE_weights),
which restricts licensed uses to noncommercial purposes and includes attribution
requirements. Review the exact model and checkpoint terms before downloading or
using them. The code license does not override the weight license or establish
rights clearance for generated content.
