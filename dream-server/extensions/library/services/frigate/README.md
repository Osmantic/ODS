# Frigate

Open-source NVR (Network Video Recorder) with real-time local AI object detection for IP cameras. Detect people, vehicles, and other objects without sending video to the cloud.

## Requirements

- **GPU:** NVIDIA (min 1 GB VRAM for object detection)
- **Dependencies:** None
- IP cameras with RTSP streams required

## Enable / Disable

```bash
dream enable frigate
dream disable frigate
```

Your data is preserved when disabling. To re-enable later: `dream enable frigate`

## Access

- **URL:** `http://localhost:8971`

## First-Time Setup

1. Enable the service: `dream enable frigate`
2. Create a `config.yml` in `./data/frigate/config/` with your camera configuration:

```yaml
mqtt:
  enabled: false

cameras:
  your_camera:
    enabled: true
    ffmpeg:
      inputs:
        - path: rtsp://user:pass@camera-ip:554/stream
          roles:
            - detect
            - record
    detect:
      width: 1920
      height: 1080
      fps: 5
```

3. Open `http://localhost:8971` to view camera feeds and detections

### Additional Ports

| Port | Description |
|------|-------------|
| 8554 | RTSP restreaming |
| 8555 | WebRTC (TCP/UDP) |

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `FRIGATE_RTSP_PASSWORD` | RTSP password for camera streams | _(required)_ |
