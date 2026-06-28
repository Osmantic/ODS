# ODS mDNS — `ods.local` from any device on your network

ODS announces itself on your local network so you can browse to it from any phone, tablet, or laptop without knowing the IP. The URL is `http://ods.local` (or `http://<your-name>.local` if you renamed the device during setup).

## Prerequisites

The mDNS announcement publishes the device's LAN IP under `<device>.local` and the proxy-routed subdomains. For `http://ods.local` to actually load chat from a phone, the `ods-proxy` extension must be enabled and LAN-bound:

1. **The `ods-proxy` extension is enabled** — that's the Caddy service that listens on port 80 and routes by hostname (`chat.<device>.local`, `dashboard.<device>.local`, `auth.<device>.local`, etc.) to the right backend.
2. **`ODS_PROXY_BIND=0.0.0.0`** — this is the proxy default. The other ODS services can remain safely loopback-bound with `BIND_ADDRESS=127.0.0.1`; only the proxy needs to listen on the LAN.

Fresh installs without the proxy are still loopback-only. The installer's first-boot wizard offers to enable the proxy path. See `docs/ODS-PROXY.md` for how the proxy routes traffic.

## What gets announced

Two flavors:

**Per-subdomain A records (the user-facing surface):**

| mDNS name | Points to | What it serves |
|---|---|---|
| `<device>.local` | LAN IP, port 80 | proxy: 302 redirect to `chat.<device>.local` |
| `chat.<device>.local` | LAN IP, port 80 | proxy → Open WebUI (root-mounted) |
| `dashboard.<device>.local` | LAN IP, port 80 | proxy → ODS Dashboard (operator UI) |
| `auth.<device>.local` | LAN IP, port 80 | proxy → dashboard-api (magic-link redemption) |
| `api.<device>.local` | LAN IP, port 80 | proxy → dashboard-api (admin `/api/*`) |
| `hermes.<device>.local` | LAN IP, port 80 | proxy → hermes-proxy (when enabled) |
| `talk.<device>.local` | LAN IP, port 80 | proxy → dashboard `/talk` (mobile owner portal, #1319) |

All seven names resolve to the same IP — the device's LAN address. Routing happens at the ods-proxy by Host header (see `docs/ODS-PROXY.md`). The bare `<device>.local` redirects to chat for the friendliest landing.

**Direct-port SRV records (back-compat for service discovery):**

These are published only when `BIND_ADDRESS` is explicitly LAN-facing (for example `0.0.0.0` or a specific LAN IP). In the default safer posture, service ports stay on loopback and mDNS publishes only the proxy-routed names above.

| mDNS name | Underlying port | Use case |
|---|---|---|
| `<device>-chat._http._tcp.local` | 3000 | Open WebUI direct (bypasses proxy) |
| `<device>-dashboard._http._tcp.local` | 3001 | Dashboard direct |
| `<device>-dashboard-api._http._tcp.local` | 3002 | Dashboard API health endpoint |
| `<device>-hermes._http._tcp.local` | 9119 | Hermes Agent direct (when the `hermes` extension is enabled) |

These exist for MCP clients, service-discovery tools, and the eventual ODS mobile app that want to talk directly to a service. End users should use the subdomain entries above.

## Platform support

| Platform | Status | Notes |
|---|---|---|
| **Linux** | ✅ supported | Uses `python3-zeroconf` against the system's `avahi-daemon` (already installed on virtually all desktop Linux distros) |
| **macOS** | ✅ implicit | macOS announces `<hostname>.local` automatically via Bonjour / mDNSResponder. The ODS mDNS script is a no-op on macOS — if you want a name other than your Mac's, change the system hostname. |
| **Windows** | ⚠️ partial | mDNS support on Windows is fragmented (Bonjour Print Services, Microsoft's own mDNS responder, varying iOS/Android interop). Not yet covered by this script; follow-up planned. |

## Troubleshooting

### "Can't reach `ods.local`"

Some routers and corporate networks block mDNS / Bonjour multicast packets:

1. **Phone can't resolve it but laptop can** — your phone may be on a separate "guest" WiFi or a 5GHz radio that's segregated from the wired network. Try connecting both to the same SSID.
2. **Nothing on the network can resolve it** — your router has IGMP snooping enabled and isn't forwarding multicast. Either flip that setting off (usually in advanced/multimedia settings) or fall back to using the device's IP address (visible in the dashboard at any time).
3. **Resolves but slow** — some Android versions cache failed mDNS lookups aggressively. Toggle WiFi off and back on, or wait a few minutes.

### Renaming the device

Edit `.env` and change `ODS_DEVICE_NAME` to whatever you want (letters, digits, hyphens; max 32 chars). The mDNS service polls `.env` every 30 seconds and re-announces automatically — no restart needed.

If you want to force immediate re-announcement: `sudo systemctl restart ods-mdns`.

### Running multiple ODSs on one network

Give each one a unique `ODS_DEVICE_NAME`. Two devices both calling themselves `ods.local` is undefined behavior — the most recent announcement usually wins but it depends on the OS and the timing. The recommended pattern: `kitchen.local`, `office.local`, `studio.local`.

### Disabling mDNS entirely

```bash
sudo systemctl stop ods-mdns
sudo systemctl disable ods-mdns
```

The device still works — you just have to use the IP address directly. The dashboard always shows the current IP in the top-right.

## What this enables

Once `ods.local` resolves on your network **and the ods-proxy is up on port 80** (see [Prerequisites](#prerequisites)), the Phase 1 onboarding UX works end to end:

1. User installs ODS (today: by running `install.sh`)
2. Device joins WiFi, starts announcing, and ods-proxy comes up on :80
3. User opens any browser on any device, types `ods.local`
4. Caddy on :80 redirects the bare hostname to `chat.<device>.local`, then routes that host to Open WebUI
5. User adds it to their phone's home screen (PWA) — the icon appears next to ChatGPT

No IP-typing, no router-config-page-diving, no DNS setup. The same UX as Sonos / Apple TV / any other consumer device on a home network.
