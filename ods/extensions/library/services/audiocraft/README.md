# AudioCraft

Meta's generative AI for audio. Features MusicGen for text-to-music generation and AudioGen for text-to-sound effects.

## Requirements

- **GPU:** NVIDIA (min 6 GB VRAM)
- **Dependencies:** None

## Apple Silicon (M1/M2/M3) note

The image build targets `linux/amd64` because the pinned Torch/AudioCraft
dependency set is for that architecture. Native ARM64 and Apple Silicon GPU
execution are not validated by this extension's image test.

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

Both models are loaded during server startup. The first startup needs network
access and enough time to download their weights before the web page is ready.
`HF_HOME=/app/models` places the Hugging Face cache in `data/audiocraft/models`;
generated audio goes to `data/audiocraft`. The host-agent lifecycle prepares
these directories for the image's non-root UID 1000. Direct Compose users and
custom/rootless UID mappings must provide matching writable bind directories.

Older definitions mounted the cache at an unused root-user path. Preserve any
needed cache from the old container before removing it, or allow the standard
weights to download again. Existing generated audio paths are unchanged.

## Image validation

The build pins AudioCraft 1.3.0 to its Torch 2.1/TorchAudio 2.1 ABI family, NumPy
1.x, compatible Transformers/Hugging Face Hub/spaCy APIs, and the Gradio 4.44
HTTP dependency set. `pip check` catches distribution conflicts; the separate
image test imports the actual packages, constructs the shipped Gradio app,
exercises its HTTP routes, and writes WAV files through the real audio writer.
Only model loading/generation is replaced with a small tensor fixture in that
test. It does not prove GPU inference or downloading the production weights.

From the repository root:

```bash
docker build -t ods-audiocraft-check ods/extensions/library/services/audiocraft
docker run --rm --network none -e GRADIO_ANALYTICS_ENABLED=False \
  -v "$PWD/ods/tests/test-audiocraft-image.py:/tmp/smoke.py:ro" \
  --entrypoint python ods-audiocraft-check /tmp/smoke.py
```

## Known Issues

The AudioCraft models are released under CC BY-NC 4.0 (non-commercial use only). Review the license terms before using generated content commercially.
