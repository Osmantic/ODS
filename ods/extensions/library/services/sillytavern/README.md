# SillyTavern

Character and roleplay chat UI that connects to local LLMs. Create characters, manage conversations, and run immersive chat experiences with ODS's local models.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
ods enable sillytavern
ods disable sillytavern
```

Your data is preserved when disabling. To re-enable later: `ods enable sillytavern`

## Access

- **URL:** `http://localhost:8001`

## First-Time Setup

1. Enable the service: `ods enable sillytavern`
2. Open `http://localhost:8001`
3. Connect to ODS's LLM by setting the API URL to `http://llama-server:8080/v1` in the connection settings
