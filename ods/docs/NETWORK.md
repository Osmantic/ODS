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

The host agent runs these `nmcli` commands with child-only `LC_ALL=C.UTF-8` and
`LANGUAGE=C` overrides
so parsed state names and error classifications do not depend on the host's
language. Other environment values, including the D-Bus address, are preserved;
the host locale and user-provided network names are unchanged. The UTF-8 C locale
retains non-ASCII names while fixing message language. This follows
[NetworkManager's scripting guidance](https://networkmanager.dev/docs/api/latest/nmcli.html).

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

The endpoint triggers a fresh rescan (best-effort) then returns nmcli's cached list. Duplicate SSIDs (multiple BSSIDs of the same network) are collapsed. Signal and security describe the strongest observed BSSID; `in_use` is true if any BSSID for that SSID is connected, even when it has a weaker signal.

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

## Operation deadlines

The dashboard's host-agent read budget includes every sequential NetworkManager step plus response overhead. Network status uses one 5-second status query and one 5-second address query for all interfaces, with a 15-second API budget. Adding interfaces does not add subprocess waits. If address collection fails, connection state is still returned with empty addresses and the host logs the reason.

Forgetting Wi-Fi allows a 10-second profile-type check followed by a 15-second delete, within a 30-second API budget. This keeps a valid deletion from completing after the dashboard has already reported an unreachable host. The existing Wi-Fi-only guard and host timeout/error responses remain in effect; the API does not retry a mutation. These are bounded I/O waits, not a guarantee during arbitrary host scheduling stalls or a lost network connection.

The batch address query uses NetworkManager's documented [`nmcli device show` behavior](https://networkmanager.dev/docs/api/latest/nmcli.html): omitting an interface examines all devices.

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
