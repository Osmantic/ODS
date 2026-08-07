# OpenCode

Browser-based AI coding assistant connected to the active ODS model.

## Overview

OpenCode runs directly on the host rather than in Docker. ODS installs it,
configures its local model route, starts its web interface on loopback, and
keeps an OpenCode entry in the dashboard application menu even while the host
process is starting or needs attention.

## Deployment

ODS manages the host process with the native user service for each platform:

| Platform | Service manager | Service |
|----------|-----------------|---------|
| Linux | systemd user service | `opencode-web.service` |
| macOS | LaunchAgent | `com.ods.opencode-web` |
| Windows | Task Scheduler | `ODSOpenCodeWeb` |

The manifest retains `type: host-systemd` for compatibility with the existing
host-service health path. `macos_host_supported: true` tells the dashboard that
the equivalent macOS LaunchAgent is available.

## Access

Open the **OpenCode** entry in the dashboard or browse directly to:

```text
http://localhost:3003
```

ODS-managed OpenCode binds only to `127.0.0.1` and opens without a separate
login on Linux, macOS, and Windows. It is not exposed through the LAN proxy.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENCODE_PORT` | `3003` | Dashboard metadata for the managed OpenCode web interface; host launchers currently use port 3003 |
| `OPENCODE_SERVER_PASSWORD` | generated | Optional password for a separately managed, network-exposed OpenCode server; ignored by the ODS loopback launcher |

ODS regenerates the managed OpenCode provider/model route during install,
upgrade, and model activation. The route follows `ods/current` when the model
switchboard is enabled.

## Requirements

- A supported ODS host on Linux, macOS, or Windows
- An active ODS local or remote model route for inference
- Enough memory for the selected model; OpenCode itself does not require 8 GB
  of VRAM

## Troubleshooting

First check whether port 3003 is listening and whether the platform service is
running:

```bash
# Linux
systemctl --user status opencode-web.service
journalctl --user -u opencode-web.service --follow
```

```bash
# macOS
launchctl print "gui/$(id -u)/com.ods.opencode-web"
tail -f "$HOME/Library/Logs/ODS/opencode-web.log"
```

```powershell
# Windows
Get-ScheduledTask -TaskName ODSOpenCodeWeb
.\ods.ps1 restart opencode
```

If the service is absent, rerun the ODS installer. If port 3003 is occupied by
another process, stop that process before reinstalling.

## Files

- `manifest.yaml`: service metadata, health route, and dashboard feature
- `ods/opencode/opencode-web.service`: Linux user service template (shared by installer phases, tracked outside this extension dir)
