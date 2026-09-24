# AudioCraft

Meta's generative AI for audio, including MusicGen for music and AudioGen for
sound effects from text. The code and model weights have different licenses;
this integration does not promise commercial clearance for generated audio.

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

Upstream distinguishes [MIT-licensed code](https://github.com/facebookresearch/audiocraft/blob/main/LICENSE)
from [CC BY-NC 4.0 model weights](https://github.com/facebookresearch/audiocraft/blob/main/LICENSE_weights).
Review the exact model terms before downloading or using it. A label such as
"royalty-free" is not commercial permission. These model terms also do not
establish ownership or clearance of every generated output.

The manifest and generated catalog still contain the older "royalty-free"
description; their runtime metadata correction is tracked separately in
[the licensing review](../../../../docs/THIRD-PARTY-LICENSING.md).
