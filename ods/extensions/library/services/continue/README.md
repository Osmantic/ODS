# Continue (AI Coding Assistant)

Open-source AI coding assistant for VS Code and JetBrains IDEs. Uses ODS's local LLM for code completion and chat — no cloud required.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** llama-server

## Enable / Disable

```bash
ods enable continue
ods disable continue
```

Your data is preserved when disabling. To re-enable later: `ods enable continue`

## Access

- **URL:** `http://localhost:8890` (config server)

## First-Time Setup

1. Enable the service: `ods enable continue`
2. Install the Continue extension in your IDE (VS Code or JetBrains)
3. Configure IDE to use `http://<ods>:8890` as the remote config server
4. Or manually set the API base URL to ODS's LLM endpoint
