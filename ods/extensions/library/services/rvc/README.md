# RVC

Retrieval-Based Voice Conversion — transform voices while preserving speaker characteristics. Open-source voice conversion framework with a web interface for easy voice manipulation.

## Requirements

- **GPU:** NVIDIA or AMD (min 6 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
ods enable rvc
ods disable rvc
```

Your data is preserved when disabling. To re-enable later: `ods enable rvc`

## Access

- **URL:** `http://localhost:7809`

The pinned RVC image has no API-key authentication. Its Gradio UI and endpoints
are unauthenticated, including when a client sends an API-key header. Keep it on
the trusted local host/network or place it behind a separately configured
authentication proxy before allowing other users to reach it.

## First-Time Setup

1. Enable the service: `ods enable rvc`
2. Open `http://localhost:7809`
3. Upload source voice audio
4. Select a pre-trained RVC model
5. Configure conversion parameters and process

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `RVC_API_KEY` | Unsupported legacy setting; any nonempty value stops startup | _(empty)_ |

Earlier documentation incorrectly described this variable as an authentication
switch. The image never reads it. The startup guard now refuses that configuration
without printing the supplied value, so an operator cannot mistake a configured
key for enforced authentication. Existing configurations with the key absent or
empty retain the upstream startup behavior. The NVIDIA entrypoint, Python server,
GPU overlays, ports and stored models are unchanged.

For an existing installation, disable RVC before applying the library update.
Updating while disabled preserves that state and installs the guard without
starting the server. An enabled update that fails startup can restore the prior
recipe through the existing rollback mechanism; an update error does not prove
that the guard is active. Check the installed definition and service state.

Before enabling the updated service, decide how access will be controlled.
Clearing the variable explicitly selects the unauthenticated local mode; it does
not secure the service. After changing configuration, recreate RVC. Rolling back
the guard restores the original behavior, including its ignored authentication
setting. Disabling RVC preserves its data.

Validation uses the exact image digest in Compose: real CPU-side Gradio startup
and HTTP access, plus rejection before server startup for configured keys. It
does not qualify voice conversion quality, CUDA/ROCm inference, proxy
authentication, or the separately supplied workflow API templates.

## Data Volumes

| Host Path | Description |
|-----------|------------|
| `./data/rvc/weights` | Model weights storage |
| `./data/rvc/opt` | Optimization files |
| `./data/rvc/dataset` | Training datasets |
| `./data/rvc/logs` | Processing logs |
