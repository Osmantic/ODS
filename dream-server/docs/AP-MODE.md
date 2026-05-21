# AP mode — first-boot Wi-Fi access point

When a Dream Server device boots fresh, it can host its own Wi-Fi network so the recipient's phone can reach the setup wizard or redeem a factory owner card without already being on a configured network. This is the "true out-of-box" flow: take it out of the box, scan QR #1 to join the AP, then scan QR #2 to open setup or Dream Talk.

This page describes the AP-mode machinery: scripts, systemd unit, host-agent endpoint, and the operator workflow to enable it.

## Why this is opt-in

**The systemd unit is shipped but disabled by default.** Bringing up an AP is destructive: it takes the wireless interface off NetworkManager, applies iptables NAT rules, and runs `hostapd` + `dnsmasq` on the host. If a user is running Dream Server on their existing laptop, auto-enabling that would disconnect them from their own Wi-Fi.

For a hardware product (Dream Mini, Strix Halo Node), the image-build pipeline can enable the unit. For the DIY install, the operator opts in explicitly.

## Architecture

```
        ┌────────── Phone (during onboarding) ──────────┐
        │  joins "Dream-Setup-XXXX" AP via QR scan      │
        │  browser opens any URL                        │
        └────────────────────┬──────────────────────────┘
                             │  HTTP
                             ▼
        ┌─────────────────────────────────────────────┐
        │  dnsmasq (on the device)                    │
        │    DHCP: hands phone an IP in 192.168.7.x   │
        │    DNS: every name → 192.168.7.1 (gateway)  │
        └─────────────────────────────────────────────┘
                             │
                             ▼
        ┌─────────────────────────────────────────────┐
        │  iptables (on the device)                   │
        │    PREROUTING: DNAT :80/:443 → 192.168.7.1  │
        │    (the gateway address — the proxy listens │
        │     there once BIND_ADDRESS=0.0.0.0)        │
        └─────────────────────────────────────────────┘
                             │
                             ▼
        ┌─────────────────────────────────────────────┐
        │  dream-proxy (Caddy) on 0.0.0.0:80          │
        │    routes /setup → dashboard:3001           │
        │    (the dashboard is loopback-bound; the    │
        │     proxy is what fields LAN traffic)       │
        └─────────────────────────────────────────────┘
                             │
                             ▼
        ┌─────────────────────────────────────────────┐
        │  dashboard:3001 (loopback)                  │
        │    serves /setup → first-boot wizard        │
        └─────────────────────────────────────────────┘

        Once the wizard completes:
          * sentinel written (PR-6)
          * operator (or PR-11) runs `systemctl disable --now dream-ap-mode`
          * NetworkManager regains wlan0, device joins the home network
```

### Prerequisites for the DNAT to actually deliver traffic

The iptables PREROUTING rule sends AP-client traffic to `192.168.7.1:80` / `:443`. For something to answer there, two things have to be true on the host:

1. **`dream-proxy` is enabled and running.** That's the Caddy service that listens on port 80 and routes `/setup`, `/chat`, `/api/*`, `/auth/*` to the right backend. Without it, AP clients hit an empty port 80 and the connection fails. The first-boot install flow enables it by default.
2. **`BIND_ADDRESS=0.0.0.0` in `.env`.** Without this, the proxy binds to `127.0.0.1:80` and the AP-side interface (`192.168.7.1`) can't reach it. The DNAT target IP would refuse the connection.

If either is missing, the captive portal redirect lands the phone on a dead port. The AP-mode systemd unit doesn't enforce these — it's the operator's responsibility to ensure the host is configured to receive what AP mode redirects.

## Components

| Component | Path | Purpose |
|---|---|---|
| `ap-mode.sh` | `scripts/ap-mode.sh` | Bring-up / tear-down / status. Reads config from `/etc/dream/ap-mode.conf`. |
| `dream-ap-mode.service` | `scripts/systemd/dream-ap-mode.service` | systemd unit. Disabled by default — enable per-device. |
| `ap-mode.conf.example` | `scripts/ap-mode.conf.example` | Annotated example operator config. |
| `/v1/ap-mode/status` | `bin/dream-host-agent.py` | Read-only status endpoint. Used by the wizard to know "am I running on the device's own AP?" |

## Setup (operator workflow)

```bash
# 1. Install hostapd, dnsmasq, iptables (NetworkManager is already required for PR-8).
sudo apt install hostapd dnsmasq iptables

# 2. Drop the operator config in place. Edit SSID + password per device.
sudo install -d -m 0755 /etc/dream
sudo install -m 0600 /dream-server/scripts/ap-mode.conf.example /etc/dream/ap-mode.conf
sudo $EDITOR /etc/dream/ap-mode.conf  # set DREAM_AP_SSID + DREAM_AP_PASSWORD

# 3. Install the systemd unit. Manual installs must render __INSTALL_DIR__
#    before copying the unit into /etc/systemd/system.
sudo cp /dream-server/scripts/systemd/dream-ap-mode.service /etc/systemd/system/
sudo sed -i 's|__INSTALL_DIR__|/dream-server|g' /etc/systemd/system/dream-ap-mode.service
sudo systemctl daemon-reload

# 4. Enable + start.
sudo systemctl enable --now dream-ap-mode

# 5. Verify.
sudo systemctl status dream-ap-mode
sudo /dream-server/scripts/ap-mode.sh status
```

When the wizard finishes and the device should join the home network instead:

```bash
sudo systemctl disable --now dream-ap-mode
```

Factory owner cards use the same AP QR for QR #1. QR #2 is the owner magic-link
URL generated from Setup / Owner, and should resolve to the LAN-local auth host
(`http://auth.<device>.local/magic-link/...`) unless the operator intentionally
prints a public/Tailscale URL.

## Config reference

All settings are bash variables sourced from `/etc/dream/ap-mode.conf`. See `scripts/ap-mode.conf.example` for the annotated version.

| Variable | Default | Notes |
|---|---|---|
| `DREAM_AP_SSID` | `Dream-Setup` | Network name. Include a per-unit suffix to avoid collisions. |
| `DREAM_AP_PASSWORD` | empty | WPA2 passphrase. Empty → open AP (allowed but warned). The example placeholder is refused so images do not ship a known password. |
| `DREAM_AP_INTERFACE` | `wlan0` | Must support AP mode. Check with `iw list \| grep -A4 'Supported interface modes' \| grep AP`. |
| `DREAM_AP_GATEWAY_IP` | `192.168.7.1` | IP that resolves every hostname (captive-portal trick). |
| `DREAM_AP_NETMASK` | `255.255.255.0` | |
| `DREAM_AP_DHCP_RANGE` | `192.168.7.10,192.168.7.50,1h` | `<start>,<end>,<lease-time>` |
| `DREAM_AP_CHANNEL` | `6` | 2.4 GHz only; 1 / 6 / 11 are the non-overlapping channels. |

## Platform support

Linux only. Tested target: Ubuntu 22.04+ / Debian 12+ / Fedora 41+ with NetworkManager. Requires:

- `hostapd` (the daemon)
- `dnsmasq` (DHCP + DNS)
- `iptables` (NAT)
- `nmcli` (to release / reclaim the wireless interface)

The script refuses to run on non-Linux or when any binary is missing — better to fail loudly than misconfigure the host.

## Known limitations

- **Driver compatibility.** Not every wireless chipset supports AP mode. Realtek's `rtl8821ce`, for instance, won't work. Check `iw list` output; the script warns when the interface doesn't advertise AP mode.
- **Only 2.4 GHz today.** 5 GHz / WiFi 6 setup adds regulatory-domain complexity that's out of scope for v1.
- **systemd-networkd hosts.** The script assumes NetworkManager. If you've switched to systemd-networkd, the "release interface" / "reclaim interface" steps won't apply cleanly. PR welcome.
- **No browser-side captive-portal detection ping.** iOS / Android probe well-known URLs (`captive.apple.com`, `connectivitycheck.gstatic.com`) on join. We catch all DNS at the AP, so those probes get the dashboard HTML and the OS marks the network as "captive." That works but isn't standards-compliant; some old Android versions may complain. Documented as a v1 trade-off.
- **No automatic teardown.** The wizard (PR-11) will call `systemctl disable --now dream-ap-mode` after setup completes. Until that lands, the operator does it manually.

## Security notes

- The unit runs `hostapd` and `dnsmasq` as root because they bind privileged sockets. That's the standard pattern; this isn't a "Dream-specific" elevation.
- The captive-portal DNS catches *every* DNS query from clients on the AP. That's intentional — it's how the wizard auto-opens. It also means malicious clients on the AP can't reach upstream services from your network during the wizard window. That's a feature.
- `iptables` rules are tagged with `--comment dream-ap-mode` so `ap-mode.sh down` removes exactly those rules and nothing else.
- **Don't expose the AP to the internet.** The dashboard's auth surface assumes a trusted LAN; the AP is part of "the device's trusted LAN" for the wizard window only.
- **Treat owner cards as keys.** Owner QR links are reusable until revoked and are not device-bound in v1. If a printed card is lost or photographed, revoke it from Setup / Owner and print a fresh card.

## Troubleshooting

### `hostapd` fails to start

Run it in the foreground to see what it complains about:

```bash
sudo hostapd /run/dream-ap-mode/hostapd.conf
```

Common causes:
- Driver doesn't support AP mode (see "Known limitations")
- Another wpa_supplicant / hostapd process holds the interface — `sudo pkill wpa_supplicant`
- Regulatory domain isn't set — `sudo iw reg set US` (or your country)

### `dnsmasq` fails to start

```bash
cat /run/dream-ap-mode/dnsmasq.log
```

Most commonly: another DNS daemon is bound to :53. `sudo systemctl stop systemd-resolved` or change the listen address.

### Phone joins the AP but doesn't open the wizard

- Verify the captive-portal redirect: `curl -v http://anything.example/` from another machine on the AP, should land on the dashboard.
- iOS sometimes caches "this network has no internet" from a previous join — Forget Network and rejoin.

### Stuck in AP mode after the wizard

`systemctl disable --now dream-ap-mode` and `sudo nmcli device set wlan0 managed yes`. NetworkManager should pick the interface back up within a few seconds.

## What's NOT here yet

- **Wizard integration** (PR-11) — detecting "running in AP mode" from the React side, showing different copy, gracefully handing off after WiFi config.
- **5 GHz support** — needs `hostapd` regulatory-domain glue.
- **Programmatic enable/disable from the dashboard API.** Deliberately omitted in this PR. Toggling an AP from an HTTP endpoint is a great way to lock yourself out of a remote box. Until we have a strong "are you sure" + recovery story, operator-only via `systemctl`.
