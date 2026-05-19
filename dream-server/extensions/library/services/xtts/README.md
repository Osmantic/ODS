# XTTS (Coqui TTS)

High-quality multilingual text-to-speech with voice cloning. Clone voices from short audio samples, supports 17 languages, and offers real-time streaming TTS with GPU acceleration.

## Requirements

- **GPU:** NVIDIA or AMD
- **Dependencies:** None

## Enable / Disable

```bash
dream enable xtts
dream disable xtts
```

Your data is preserved when disabling. To re-enable later: `dream enable xtts`

## Access

- **API:** `http://localhost:8100`

## First-Time Setup

1. Enable the service: `dream enable xtts`
2. Send POST requests to the TTS API at `http://localhost:8100`

### Example Request

```bash
curl -X POST http://localhost:8100/tts \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Hello, this is a test.",
    "speaker_wav": "speaker.wav",
    "language": "en"
  }'
```
