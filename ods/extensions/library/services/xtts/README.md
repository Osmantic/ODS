# XTTS (Coqui TTS)

High-quality multilingual text-to-speech with voice cloning. Clone voices from short audio samples, supports 17 languages, and offers real-time streaming TTS with GPU acceleration.

## Requirements

- **GPU:** NVIDIA or AMD
- **Dependencies:** None

## Enable / Disable

```bash
ods enable xtts
ods disable xtts
```

Your data is preserved when disabling. To re-enable later: `ods enable xtts`

## Access

- **API:** `http://localhost:8100`

## First-Time Setup

1. Enable the service: `ods enable xtts`
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

## Model terms and acceptance

XTTS-v2 weights use the [Coqui Public Model License](https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt),
which limits use of the model and its outputs to its defined noncommercial
purposes unless separately licensed. Review the terms and notice obligations
before enabling this service. The ODS code license does not replace them.

**Current integration limitation:** the Compose recipe hardcodes
`COQUI_TOS_AGREED=1`. It therefore supplies upstream's acceptance signal without
collecting an explicit, recorded choice from the operator. Enabling the service
is not evidence that such a choice was collected, or that a commercial license
exists. Do not use the recipe until you have independently established that your
intended use is permitted. An explicit acceptance flow is a runtime follow-up;
this documentation update does not change the recipe.
