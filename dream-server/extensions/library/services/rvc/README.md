# RVC

Retrieval-Based Voice Conversion — transform voices while preserving speaker characteristics. Open-source voice conversion framework with a web interface for easy voice manipulation.

## Requirements

- **GPU:** NVIDIA or AMD (min 6 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
dream enable rvc
dream disable rvc
```

Your data is preserved when disabling. To re-enable later: `dream enable rvc`

## Access

- **URL:** `http://localhost:7809`

## First-Time Setup

1. Enable the service: `dream enable rvc`
2. Open `http://localhost:7809`
3. Upload source voice audio
4. Select a pre-trained RVC model
5. Configure conversion parameters and process

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `RVC_API_KEY` | API key for authentication (optional; leave empty to disable) | _(empty)_ |

## Data Volumes

| Host Path | Description |
|-----------|------------|
| `./data/rvc/weights` | Model weights storage |
| `./data/rvc/opt` | Optimization files |
| `./data/rvc/dataset` | Training datasets |
| `./data/rvc/logs` | Processing logs |
