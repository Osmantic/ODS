# Network configuration (Wi-Fi management)

ODS can join the host to a Wi-Fi network through the dashboard. This is wired into the first-boot wizard but the endpoints are also callable directly.

## Platform support

| OS / stack | Supported | Notes |
|---|---|---|
| Linux + NetworkManager | ✅ | Primary target. Ubuntu 22.04+, Debian 12+, Fedora 41+, most desktop distros ship `nmcli` by default. |
| Linux + systemd-networkd / wpa_supplicant-only | ❌ | The endpoints return `501` with a clear error. Configure manually until we add this. |
| macOS | ❌ | The system controls Wi-Fi. The endpoints return a clear "not supported" response and the wizard falls back to "use Ethernet." |
| Windows | ❌ | Same as macOS. |

The dashboard's `/api/setup/network-status` always returns `200` (never `5xx`) on unsupported platforms — the body carries `platform_supported: false` so the wizard can render a fallback without error handling.

## Architecture

```
Dashboard React UI
       │ /api/setup/wifi-scan
       │ /api/setup/wifi-connect
       │ /api/setup/network-status
       ▼
dashboard-api (FastAPI, container)
       │ /v1/network/...
       ▼
ods-host-agent (HTTP server on host, root)
       │ subprocess
       ▼
   nmcli ─→ NetworkManager
```

The container can't run `nmcli` directly — it needs root and access to the host's NetworkManager D-Bus. Routing through the host-agent is the same pattern we already use for `.env` writes and Docker recreates.

## API surface

All endpoints require the standard dashboard-api Bearer token (auth handled at the dashboard-api edge; the host-agent has its own API key for the inner hop).

### `GET /api/setup/wifi-scan`

Returns nearby Wi-Fi networks, strongest signal first.

```json
{
  "networks": [
    {"ssid": "Home WiFi", "signal": 88, "security": "WPA2", "in_use": true},
    {"ssid": "Guest",     "signal": 50, "security": "WPA2", "in_use": false}
  ]
}
```

The endpoint triggers a fresh rescan (best-effort) then returns nmcli's cached list. Duplicate SSIDs (multiple BSSIDs of the same network) are collapsed.

### `POST /api/setup/wifi-connect`

Joins a Wi-Fi network.

```json
{ "ssid": "Home WiFi", "password": "supersecret" }
```

Returns `{"success": true, "ssid": "..."}` on success.

Error responses:
- `400 Wrong password` — auth failed
- `400 Network not found` — SSID is not visible
- `504 Connection attempt timed out` — handshake / DHCP didn't complete in 45s
- `501` — host is not Linux + NetworkManager
- `503` — host-agent itself is unreachable

The password is **never** logged. The host-agent passes it to nmcli via argv; the only thing in the log is `wifi-connect ssid=<name> password_set=true`.

### `GET /api/setup/network-status`

Current connectivity. Always returns 200.

```json
{
  "platform_supported": true,
  "devices": [
    {
      "device": "wlan0",
      "type": "wifi",
      "state": "connected",
      "connection": "Home WiFi",
      "ip": "192.168.1.42",
      "gateway": "192.168.1.1"
    }
  ],
  "wifi_connected": true
}
```

On unsupported platforms:

```json
{ "platform_supported": false, "platform": "Windows", "reason": "..." }
```

### `POST /api/setup/wifi-forget`

Deletes a saved NetworkManager connection profile.

```json
{ "connection": "OldNetwork" }
```

## Security notes

- **Password lifetime in process memory.** The password lives in the host-agent's memory while the subprocess runs, then in nmcli's argv until the process exits. On modern Linux with `kernel.yama.ptrace_scope >= 1` (default on Ubuntu/Fedora), unprivileged processes can't read the cmdline of another user's process — and the host-agent runs as root anyway. The exposure window is acceptable for v1.
- **Not for hostile networks.** This is a local-LAN admin surface. Don't expose the dashboard-api to the public internet without a real auth layer in front.
- **Connection profiles persist.** Once connected, NetworkManager remembers the password. `/api/setup/wifi-forget` is how you remove it.

## Troubleshooting

### `nmcli not found`

The host-agent returns `501`. Install NetworkManager via your distro:

```bash
# Debian / Ubuntu
sudo apt install network-manager

# Fedora
sudo dnf install NetworkManager

# Arch
sudo pacman -S networkmanager
```

Some distros use systemd-networkd by default; switching to NetworkManager is the supported path today.

### Scan returns no networks

- The radio may be soft-blocked. Run `rfkill list` and unblock with `rfkill unblock wifi`.
- If running in a container/VM, the host needs Wi-Fi hardware passthrough; running in a generic VM almost never works.
- Some hardware needs proprietary firmware (e.g. Broadcom). Check `dmesg | grep firmware`.

### Connect succeeds but no IP

NetworkManager handles DHCP; if the AP authenticated you but no IP arrives, the upstream DHCP server is the problem. Verify with `nmcli connection show <ssid>` then `nmcli connection up <ssid>`.

### Two networks with the same SSID

The scan collapses on SSID. If you genuinely need to target a specific BSSID, use `nmcli` directly — the wizard intentionally does not surface BSSID selection.
