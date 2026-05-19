# Piper TTS

Fast, local neural text-to-speech system optimized for edge devices. Uses the Wyoming protocol for integration with Home Assistant and other services. Multiple voice models available across many languages.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon (CPU-based, no GPU required)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable piper-audio
dream disable piper-audio
```

Your data is preserved when disabling. To re-enable later: `dream enable piper-audio`

## Access

- **Wyoming Protocol:** `tcp://localhost:10200`

## First-Time Setup

1. Enable the service: `dream enable piper-audio`
2. Connect via Wyoming protocol at `tcp://localhost:10200`
3. Optionally change the voice model via the `PIPER_VOICE` environment variable

### Popular Voices

- `en_US-lessac-medium` (default, high quality)
- `en_US-amy-medium`
- `en_GB-southern_english_male-medium`

Full list: https://huggingface.co/rhasspy/piper-voices/tree/main

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `PIPER_VOICE` | Default voice model | `en_US-lessac-medium` |
