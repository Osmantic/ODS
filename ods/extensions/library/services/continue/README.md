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

1. Set `CONTINUE_API_BASE` in ODS's `.env` to
   the **complete API base reachable from the computer running your IDE**.
   For example, `http://127.0.0.1:11434/v1` for Linux's same-host published
   llama.cpp endpoint, `http://127.0.0.1:8080/v1` for native macOS, or
   `http://127.0.0.1:8080/api/v1` for a Windows Lemonade endpoint.
   Use the actual published port and API path for your installation. A remote
   IDE needs an address or tunnel it can reach; Docker service names such as
   `ods-llama-server` do not resolve there. This setting does not expose a port
   or change ODS's default loopback binding.
2. Enable the service: `ods enable continue`. An unset or malformed API base
   stops configuration generation with an actionable error.
3. Install the Continue extension in your IDE (VS Code or JetBrains).
4. Download `http://localhost:8890/config.yaml` from the config server (adjust
   the address and `CONTINUE_PORT` as needed). Back up your existing
   `~/.continue/config.yaml`, then merge the generated `models` entries into it.
   If you have no config, use the downloaded file as your initial config.
   Continue's [YAML configuration](https://docs.continue.dev/reference) uses
   `apiBase`; it does not use the legacy JSON `remoteConfigServerUrl` setting.
5. If the endpoint requires authentication, add `apiKey` to each model in your
   private IDE config. Never put credentials in `CONTINUE_API_BASE`: the
   generated file is served without authentication. Select a concrete model
   advertised by your endpoint if `AUTODETECT` is unsuitable for that provider.

Both chat and autocomplete receive the complete base unchanged (apart from a
trailing slash). Explicit `/v1`, `/api/v1`, and reverse-proxy prefixes are
preserved. The generator does not append another `/v1` or reuse the internal
`LLM_API_URL`.

After changing this setting, recreate the Continue container to regenerate the
file, then download and merge it again; the local IDE copy is not automatically
updated. Existing installations must set `CONTINUE_API_BASE` before updating.
Rollback restores the old generator; retain your working local IDE config and
its backup when doing so. Configuration generation and HTTP delivery can be
tested without a model; actual chat and autocomplete also require a running,
compatible model endpoint.
