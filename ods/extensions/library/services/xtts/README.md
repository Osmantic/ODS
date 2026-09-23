# XTTS (Coqui TTS)

High-quality multilingual text-to-speech with voice cloning. Clone voices from short audio samples, supports 17 languages, and offers real-time streaming TTS with GPU acceleration.

## Requirements

- **GPU:** NVIDIA or AMD
- **Dependencies:** None

## Enable / Disable

Review the [XTTS-v2 model license](https://huggingface.co/coqui/XTTS-v2/blob/6c2b0d75eae4b7047358e3b6bd9325f857d43f77/LICENSE.txt) and [recipe provenance notice](NOTICE.md) first. The published CPML permits non-commercial use subject to its terms; the API server's MIT license does not cover model weights.

If you agree to the applicable model terms, explicitly set `COQUI_TOS_AGREED=1` in your ODS installation's `.env` before enabling XTTS. ODS does not set this value or infer acceptance from a download. Missing or empty values fail Compose configuration before container creation; `0`, `true`, and any value other than `1` are rejected before starting the server. This environment setting records your operator choice; it is not a legal review or an acceptance UI.

```bash
ods enable xtts
ods disable xtts
```

Your data is preserved when disabling. To re-enable later: `ods enable xtts`

## Access

- **API:** `http://localhost:8100`

## First-Time Setup

1. Review the model terms and explicitly configure `COQUI_TOS_AGREED=1` as described above.
2. Enable the service: `ods enable xtts`
3. Send POST requests to the TTS API at `http://localhost:8100`

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
