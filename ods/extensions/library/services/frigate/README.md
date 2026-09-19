# Frigate

Open-source NVR (Network Video Recorder) with real-time local AI object detection for IP cameras. Detect people, vehicles, and other objects without sending video to the cloud.

## Requirements

- **GPU:** NVIDIA (min 1 GB VRAM for object detection)
- **Dependencies:** None
- IP cameras with RTSP streams required

## Enable / Disable

```bash
ods enable frigate
ods disable frigate
```

Your data is preserved when disabling. To re-enable later: `ods enable frigate`

## Access

- **URL:** `https://localhost:8971`

The published browser port now reaches Frigate's authenticated HTTPS listener
(`8971` inside the container). The older recipe published the internal port
`5000`, which bypasses Frigate login. Port `5000` remains available only on the
Docker network for the existing internal health probe and trusted integrations.
Treat access to that Docker network as trusted; this change does not add an
authentication boundary between containers already on the network.

On first startup, Frigate prints its generated administrator credentials in
`docker logs ods-frigate`. Sign in and change the initial password under
Settings → Users. The RTSP camera password is **not** the web administrator
password. Accounts and the JWT secret persist under `data/frigate/config`.

Frigate generates a self-signed TLS certificate by default. Verify the server's
identity and configure a trusted certificate or your own TLS proxy for remote
access. Reverse proxies should target container port `8971`, not `5000`.
If an existing configuration explicitly disables TLS, either re-enable it or
configure `FRIGATE_PUBLIC_URL` in the installation `.env` to match your deliberate
proxy/HTTP endpoint; the default dashboard link now uses HTTPS.

Before updating an existing installation, retain a cold backup of its complete
configuration/database directory and check that `auth.enabled` has not been
explicitly disabled. This port change preserves that operator configuration;
it cannot enforce login when the application has been configured to disable it.

## First-Time Setup

1. Enable the service: `ods enable frigate`
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

3. Open `https://localhost:8971` and sign in to view camera feeds and detections

### Additional Ports

| Port | Description |
|------|-------------|
| 8554 | RTSP restreaming |
| 8555 | WebRTC (TCP/UDP) |

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `FRIGATE_RTSP_PASSWORD` | RTSP password for camera streams | _(required)_ |

The RTSP/WebRTC ports and camera configuration are unchanged by the authenticated
web-port fix. Configure their access controls separately. A rollback to the old
port mapping removes browser login enforcement again; retain the authenticated
mapping when rolling back unrelated changes. The boundary test uses no cameras
and verifies HTTP authentication and account persistence, not detection or video
capture. See [upstream authentication](https://docs.frigate.video/configuration/authentication/)
and [TLS configuration](https://docs.frigate.video/configuration/tls).
