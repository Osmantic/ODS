# OpenCode

Browser-based AI coding assistant connected to the active ODS model.

## Overview

OpenCode runs directly on the host rather than in Docker. ODS installs it,
configures its local model route, and starts its web interface on loopback.
On Linux it is an opt-in extension; the macOS and Windows installers always set
it up.

The host agent reports OpenCode's real lifecycle to the dashboard:

| State | Meaning | Dashboard |
|-------|---------|-----------|
| Running | `GET /global/health` answers on the OpenCode port | **Applications -> OpenCode** opens it in a new tab |
| Starting | The service manager is starting it | Entry shows *Starting* |
| Stopped | ODS set it up, but it is not running | Entry shows *Stopped*; the OpenCode page has **Start OpenCode** |
| Not set up | No ODS-managed OpenCode service exists | No Applications entry; set it up from **Extensions** or the OpenCode page (Linux) |

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

Open **Applications -> OpenCode** in the dashboard sidebar, the **OpenCode**
page (`/apps/opencode`), or browse directly to:

```text
http://localhost:3003
```

ODS-managed OpenCode binds only to `127.0.0.1` and opens without a separate
login on Linux, macOS, and Windows. It is not exposed through the LAN proxy.
Because OpenCode can read and change files and run commands as your user, ODS
never publishes it to the network. When the dashboard is opened from another
device, the OpenCode page shows an SSH port-forward instead of a dead link:

```bash
ssh -N -L 3003:127.0.0.1:3003 <user>@<ods-host>
# then open http://localhost:3003 on that device
```

If you run your own authenticated reverse proxy in front of it, set
`OPENCODE_PUBLIC_URL` and the dashboard links there instead.

### Set up or start from the dashboard

- **Linux, not set up:** Extensions -> OpenCode -> **Install**, or the OpenCode
  page -> **Set up OpenCode**. The host agent installs the reviewed release from
  `installers/lib/opencode-release.tsv` (SHA-256 verified), writes the model
  route used by model activation, renders `opencode-web.service`, and starts it.
  A later Linux installer rerun without `--opencode` still retires the service,
  as it does for an installer opt-in; pass `--opencode` to keep it.
- **Stopped:** the OpenCode page -> **Start OpenCode** (systemd user unit,
  LaunchAgent, or the `ODSOpenCodeWeb` scheduled task).

In a terminal on the ODS machine you can attach to the same sessions:

```bash
~/.opencode/bin/opencode attach http://localhost:3003
```

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
- `opencode-web.service`: Linux user service template
