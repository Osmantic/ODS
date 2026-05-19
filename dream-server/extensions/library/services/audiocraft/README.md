# AudioCraft

Meta's generative AI for audio. Features MusicGen for text-to-music generation and AudioGen for text-to-sound effects — create royalty-free music and sound effects from text descriptions.

## Requirements

- **GPU:** NVIDIA (min 6 GB VRAM)
- **Dependencies:** None

## Apple Silicon (M1/M2/M3) note

This extension is configured `platform: linux/amd64` because some of its Python dependencies don't have native ARM64 wheels. On Apple Silicon, Docker Desktop runs it under QEMU x86_64 emulation — expect noticeably slower builds (typically 5–10x) and reduced runtime CPU performance (typically 2–5x) compared to native ARM64 hosts. Functional but not recommended for active iterative work on Apple Silicon.

## Enable / Disable

```bash
dream enable audiocraft
dream disable audiocraft
```

Your data is preserved when disabling. To re-enable later: `dream enable audiocraft`

## Access

- **URL:** `http://localhost:7863`

## First-Time Setup

1. Enable the service: `dream enable audiocraft`
2. Open `http://localhost:7863`
3. Use the MusicGen tab to generate music from text descriptions
4. Use the AudioGen tab to generate sound effects

Models are downloaded automatically on first use.

## Known Issues

The AudioCraft models are released under CC BY-NC 4.0 (non-commercial use only). Review the license terms before using generated content commercially.
