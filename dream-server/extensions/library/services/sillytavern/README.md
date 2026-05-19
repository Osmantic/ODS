# SillyTavern

Character and roleplay chat UI that connects to local LLMs. Create characters, manage conversations, and run immersive chat experiences with Dream Server's local models.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
dream enable sillytavern
dream disable sillytavern
```

Your data is preserved when disabling. To re-enable later: `dream enable sillytavern`

## Access

- **URL:** `http://localhost:8001`

## First-Time Setup

1. Enable the service: `dream enable sillytavern`
2. Open `http://localhost:8001`
3. Connect to Dream Server's LLM by setting the API URL to `http://llama-server:8080/v1` in the connection settings
