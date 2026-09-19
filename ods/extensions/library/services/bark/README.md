# Bark TTS

Transformer-based text-to-audio model by Suno AI. Generates highly expressive, realistic speech including laughter, sighs, and emotion — far beyond traditional TTS. Supports 13 languages with multiple voice presets.

## Requirements

- **GPU:** NVIDIA (min 4 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
ods enable bark
ods disable bark
```

Your data is preserved when disabling. To re-enable later: `ods enable bark`

## Access

- **URL:** `http://localhost:9200` (API docs)

## First-Time Setup

1. Enable the service: `ods enable bark`
2. Wait for first startup to download ~5 GB of models (10-20 minutes)
3. Access the API at `http://localhost:9200`

### API Examples

```bash
# Generate speech (Base64 response)
curl -X POST http://localhost:9200/tts \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello! [laughs] This is Bark TTS.", "voice_preset": "v2/en_speaker_6"}'

# Get raw audio (WAV)
curl -X POST http://localhost:9200/tts/stream \
  -H "Content-Type: application/json" \
  -d '{"text": "Hello from Bark!", "voice_preset": "v2/en_speaker_3"}' \
  --output output.wav

# List voice presets
curl http://localhost:9200/voices
```

### Concurrent requests

Both synthesis routes share one generation slot because Bark reuses model state,
including CPU/GPU placement. While a generation is running, another synthesis
request receives HTTP **503** immediately; requests are not queued in memory.
Wait for the active job to finish before submitting another. `/health` and
`/voices` remain available. The slot is released after computation succeeds or
fails, not merely when an HTTP caller stops waiting. Run the shipped single
Uvicorn worker; adding workers duplicates the models and bypasses this
process-local admission limit.

### Special Text Tokens

Bark understands non-verbal cues in brackets: `[laughter]`, `[sighs]`, `[music]`, `[gasps]`, `[clears throat]`, `...` (pauses), `♪` (singing mode).

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `BARK_USE_SMALL_MODELS` | Use smaller/faster models (less VRAM) | `false` |
| `BARK_OFFLOAD_CPU` | Offload to CPU between requests | `false` |
| `BARK_API_KEY` | API key for authentication (optional) | _(empty)_ |
