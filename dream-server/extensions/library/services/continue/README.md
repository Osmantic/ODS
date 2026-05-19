# Continue (AI Coding Assistant)

Open-source AI coding assistant for VS Code and JetBrains IDEs. Uses Dream Server's local LLM for code completion and chat — no cloud required.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** llama-server

## Enable / Disable

```bash
dream enable continue
dream disable continue
```

Your data is preserved when disabling. To re-enable later: `dream enable continue`

## Access

- **URL:** `http://localhost:8890` (config server)

## First-Time Setup

1. Enable the service: `dream enable continue`
2. Install the Continue extension in your IDE (VS Code or JetBrains)
3. Configure IDE to use `http://<dream-server>:8890` as the remote config server
4. Or manually set the API base URL to Dream Server's LLM endpoint
