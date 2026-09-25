# IT-Tools for ODS

Developer utilities: JSON/YAML/TOML conversion, text/JSON differences, hashes,
QR codes, subnet calculations and other upstream tools. Counted as one extension.

## Installation and use

Install and enable `it-tools` from the ODS library. Open its launch action or
`http://localhost:11059`. `IT_TOOLS_PORT` overrides the loopback host port.
Choose a tool and supply its input explicitly. There is no automatic connection
to Portal conversations, project files, clipboard or the selected model.

The official GPL-3.0 application's static assets are copied from the pinned
`2024.10.22-7ca5933` image. This was the latest GitHub release found on
2026-09-20; it does not imply that unreleased source changes are included.
A separately pinned nginx 1.28 Alpine serves those assets under UID 101,
read-only except for bounded temporary storage. SPA routes fall back to the
application entry point so reloading a tool URL works.

## Storage and platform behavior

No account or server database is involved. Preferences belong to this browser
origin; clearing browser data or changing the port can remove/isolate them.
Export or copy results using each tool before clearing its inputs. No empty
Docker data volume is presented as a backup mechanism.

Published application assets support Linux amd64 and arm64. Use Linux containers
on Docker Desktop for Windows/macOS or Docker Engine on Linux. No GPU, inference
server, model weights or paid API credential is required. Clipboard/camera
features depend on browser permission and secure-context support; they are not
enabled automatically. Tools that contact an external service remain subject to
that tool's behavior; this integration does not assert universal offline operation.

## Verification boundary

Health checks confirm the local HTTP server answers. They do not prove individual
conversions or browser API functionality. Image build, UI interactions, deep-link
reload and browser persistence remain runtime-pending. No application was started
as part of this integration work.

Upstream: https://github.com/CorentinTh/it-tools
