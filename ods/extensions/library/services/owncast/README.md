# Owncast

MIT Owncast v0.3.0 with the official digest-pinned image and bundled FFmpeg.
Stream video from an RTMP encoder such as OBS to the native browser player/chat.
This is a live-streaming server, not a media library or an automatic recording app.

## Owner setup and broadcasting

Configure distinct random 64-hex `OWNCAST_ADMIN_PASSWORD` and
`OWNCAST_STREAM_KEY`. Open `http://localhost:11129/admin` and use the native
administrator login (`admin` and the configured password). Review instance title,
audience/chat settings and video output profiles before broadcasting.

In OBS on this computer, select a custom streaming service with server
`rtmp://127.0.0.1:11130/live` and the configured stream key. Start streaming only
when ready. The player is `http://localhost:11129`. An encoder container on the
ODS network instead uses `rtmp://owncast:1935/live`.

Both published ports are loopback-only. Other devices need deliberate reachable
ingress, firewall rules and HTTPS for browser access; their own localhost is not
this server. No public tunnel, certificate, federation/directory registration,
encoder or stream is configured by ODS. Viewer access is not protected by the
administrator password: review access requirements before exposing the player.

The deployment enforces both credentials through native startup flags. The admin
password is reapplied at every start; an admin-page password change is superseded
at restart unless the extension configuration is also updated. The supplied stream
key is a native process-lifetime override. Rotate it in the extension configuration
and update encoders. These flags are visible to host/container administrators via
process inspection; do not share diagnostic process output containing them.

## Data and capacity

`owncast-data` persists `/app/data`, including SQLite settings, native backups,
uploads, logs and temporary/HLS data. Retain it on reinstall; stop the service
before copying the entire volume or use native consistent database backups plus
the associated files. HLS segments are delivery artifacts, not a guaranteed
permanent recording. Configure recording separately in the encoder if needed.

The recipe runs as UID/GID 101 with read-only application files, writable data and
temporary storage, up to four CPUs and 2 GiB RAM. Transcoding is CPU-based; no GPU
device is requested. Choose output count, resolution and bitrate for the host.
Multiple renditions may exceed small-computer capacity; the recipe does not claim
one preset fits every machine. Disk use also depends on stream/settings behavior.

Linux amd64/arm64 containers work through Docker Engine or Windows/macOS Docker
Desktop. Native `/api/status` health indicates server response, not an active
stream, successful encoding or playback quality. Initial credentials, RTMP ingest,
browser playback/chat and restore/platform runtime remain validation pending.
No application, model or encoder was started while preparing this integration.

Sources: [Owncast v0.3.0](https://github.com/owncast/owncast/tree/v0.3.0),
[startup flags](https://github.com/owncast/owncast/blob/v0.3.0/main.go),
[quickstart](https://owncast.online/docs/quickstart/).
