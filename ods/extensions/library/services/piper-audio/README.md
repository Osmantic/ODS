# Piper TTS

Fast, local neural text-to-speech system optimized for edge devices. Uses the Wyoming protocol for integration with Home Assistant and other services. Multiple voice models available across many languages.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon (CPU-based, no GPU required)
- **Dependencies:** None

## Enable / Disable

```bash
ods enable piper-audio
ods disable piper-audio
```

Your data is preserved when disabling. To re-enable later: `ods enable piper-audio`

## Voice model storage

The shipped LinuxServer Piper 1.6.3 process uses `/config` for both voice lookup
and downloads. ODS mounts `data/piper` there, so `.onnx` models and their matching
`.onnx.json` configuration files survive container recreation. Put custom voice
pairs directly in `data/piper`, then select their voice name with `PIPER_VOICE`.

Older ODS definitions mounted only `/config/piper`. The image declares a volume
at the parent `/config`, so downloaded models went to an anonymous Docker volume
outside `data/piper`. Compose can reuse that anonymous volume during some
recreations, but removing the container and creating it again does not reconnect
that cache. Before replacing an old container, preserve files you need:

```bash
# Run from the ODS install directory, while the old container still exists.
docker stop ods-piper-audio
mkdir piper-config-backup
docker cp ods-piper-audio:/config/. piper-config-backup/
```

Keep that backup and the existing `data/piper` directory. Copy the required voice
`.onnx`/`.onnx.json` pairs from the backup's top level into `data/piper`, checking
for same-named files before replacing anything. Apply the updated definition and
recreate through ODS, then verify the configured voice before removing backups.
Standard voices can be downloaded again. After container removal, recovery of
custom voices requires a backup or identifying the retained anonymous volume;
removing that volume also removes its files. If the update is postponed, start
the stopped container with `docker start ods-piper-audio`.

On rollback, retain the `/config` mount target to keep using the persistent voice
directory. The change affects storage placement only; Wyoming's port and voice
selection are unchanged. On Windows, run the commands in the ODS WSL terminal.

## Access

- **Wyoming Protocol:** `tcp://localhost:10200`

## First-Time Setup

1. Enable the service: `ods enable piper-audio`
2. Connect via Wyoming protocol at `tcp://localhost:10200`
3. Optionally change the voice model via the `PIPER_VOICE` environment variable

### Popular Voices

- `en_US-lessac-medium` (default, high quality)
- `en_US-amy-medium`
- `en_GB-southern_english_male-medium`

Full list: https://huggingface.co/rhasspy/piper-voices/tree/main

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `PIPER_VOICE` | Default voice model | `en_US-lessac-medium` |
