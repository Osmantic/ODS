# Bark TTS

Transformer-based text-to-audio model by Suno AI. Generates highly expressive, realistic speech including laughter, sighs, and emotion — far beyond traditional TTS. Supports 13 languages with multiple voice presets.

## Requirements

- **GPU:** NVIDIA (min 4 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable bark
dream disable bark
```

Your data is preserved when disabling. To re-enable later: `dream enable bark`

## Access

- **URL:** `http://localhost:9200` (API docs)

## First-Time Setup

1. Enable the service: `dream enable bark`
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

### Special Text Tokens

Bark understands non-verbal cues in brackets: `[laughter]`, `[sighs]`, `[music]`, `[gasps]`, `[clears throat]`, `...` (pauses), `♪` (singing mode).

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `BARK_USE_SMALL_MODELS` | Use smaller/faster models (less VRAM) | `false` |
| `BARK_OFFLOAD_CPU` | Offload to CPU between requests | `false` |
| `BARK_API_KEY` | API key for authentication (optional) | _(empty)_ |
