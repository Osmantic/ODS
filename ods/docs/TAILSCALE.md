# Tailscale (remote access)

ODS includes an optional Tailscale extension that puts the device on your Tailscale net (the mesh VPN at `tailscale.com`). Once joined, the device gets a private tailnet IP/DNS name; with `ods-proxy` enabled and `BIND_ADDRESS=0.0.0.0`, the dashboard and chat are reachable from any other tailnet member — your laptop, your phone, a friend's machine — anywhere with internet.

This is how you reach `ods.local` from the coffee shop without exposing anything to the public internet.

## Why Tailscale (vs. exposing ports / port-forwarding / Cloudflare Tunnel)

- **Zero NAT / port-forwarding config.** You don't touch your router. Tailscale's mesh handles NAT traversal.
- **Identity-based access.** Only devices signed into your tailnet can reach ODS. No "the URL leaked" failure mode.
- **End-to-end encrypted.** WireGuard under the hood.
- **No public endpoint.** The device is invisible to the open internet — there's no `odsXYZ.com` listing to attack.

Tradeoff: every device that needs to reach ODS has to install the Tailscale client. For "me and my family" that's fine. For "publish this to anyone on the internet," you'd want Cloudflare Tunnel + auth instead — out of scope for this extension.

## Architecture

```
        ┌────────── Your phone (away from home) ──────────┐
        │  Tailscale client running                       │
        │  browser opens http://ods.tail-xxxxx.ts.net   │
        └──────────────────────┬──────────────────────────┘
                               │
                               │  encrypted WireGuard
                               ▼
        ┌────────────────────────────────────────────────┐
        │  Tailscale relay or direct path (depends on NAT)│
        └──────────────────────┬─────────────────────────┘
                               │
                               ▼
        ┌────────────────────────────────────────────────┐
        │  ODS host (Linux, with                │
        │     ods-tailscale container in host-net mode)│
        │                                                │
        │  Container's tailscaled exposes the HOST'S     │
        │  network namespace to the tailnet. The         │
        │  reachable surface is whatever the host        │
        │  itself is listening on. With ods-proxy on   │
        │  port 80 and BIND_ADDRESS=0.0.0.0, that's the  │
        │  same /chat, /api/*, /auth/* paths users see   │
        │  on the LAN.                                   │
        └────────────────────────────────────────────────┘
```

The container uses `network_mode: host` so the Tailscale daemon and the dashboard share the same network namespace. **What that buys you depends entirely on what the host is binding to** (see Prerequisites below).

## Prerequisites for tailnet reachability

Joining the tailnet only gets the device a `100.x.y.z` IP and a `ods.tail-xxxxx.ts.net` DNS name. For an HTTP request from another tailnet member to actually load chat, the host has to be listening on the right port and address:

1. **`ods-proxy` is enabled.** It listens on port 80 and routes `/chat`, `/api/*`, `/auth/*` to the right backend. With it, tailnet clients browse to `http://ods.tail-xxxxx.ts.net` (no port). Without it, only the per-service ports work (`:3000`, `:3001`, `:3002`) and only if those are LAN-bound.
2. **`BIND_ADDRESS=0.0.0.0` in `.env`.** The default `127.0.0.1` means the proxy / dashboard / chat only accept loopback connections — even though tailscaled is in the same namespace, an incoming tailnet packet looks like any other LAN packet, not loopback. Set `BIND_ADDRESS=0.0.0.0` so the host's listen sockets accept those connections.

If you ship a fresh install with Tailscale enabled but neither of the above, `tailscale status` will show the device authed but `http://ods.tail-xxxxx.ts.net` will hang or refuse. Until the dashboard gets a dedicated Remote Access UI, verify those prerequisites with `.env`, `ods list`, and the status endpoint below.

## Setup

The extension is **opt-in**. It doesn't auto-install; you enable it when you want remote access.

```bash
# 1. Generate an auth key.
# In the Tailscale admin console (https://login.tailscale.com/admin/settings/keys):
#   - Reusable: usually yes (so you don't have to mint a fresh key every reinstall)
#   - Ephemeral: NO (the device stays in your tailnet after the daemon restarts)
#   - Tags: optional. If your ACL defines tag:ods and your key is allowed
#     to use it, set TS_EXTRA_ARGS=--advertise-tags=tag:ods below.

# 2. Drop the key into .env:
cat >> .env <<'EOF'
TS_AUTHKEY=tskey-auth-xxxxxxxxxxxxxxxxxxxxxx
TS_HOSTNAME=                # leave blank — defaults to ODS_DEVICE_NAME
TS_EXTRA_ARGS=              # optional, e.g. --advertise-tags=tag:ods
EOF

# 3. Enable the extension.
ods enable tailscale
# (Or from the dashboard: Extensions → Tailscale → Enable.)

# 4. The container starts, joins the tailnet, and shows up in
#    `tailscale status` on every other tailnet member within seconds.
```

## Verifying

From the device itself:

```bash
docker exec ods-tailscale tailscale status
# Should show this device authed + your tailnet name
```

From the dashboard API:

```bash
curl -H "Authorization: Bearer ${DASHBOARD_API_KEY}" \
  http://localhost:3002/api/tailscale/status
# {
#   "running": true,
#   "authenticated": true,
#   "self": {
#     "hostname": "ods",
#     "dns_name": "ods.tail-abcde.ts.net",
#     "ips": ["100.64.0.42", ...],
#     "online": true
#   },
#   "magic_dns_suffix": "tail-abcde.ts.net"
# }
```

From any other device on your tailnet:

```bash
# After installing the Tailscale client and signing into the same tailnet,
# AND with ods-proxy enabled + BIND_ADDRESS=0.0.0.0 on the device:
curl http://ods.tail-abcde.ts.net/api/status
# Or from a browser: http://ods.tail-abcde.ts.net (lands at /chat via the proxy)

# To bypass the proxy and hit a specific service directly, use its port
# (only works when BIND_ADDRESS=0.0.0.0):
curl http://ods.tail-abcde.ts.net:3001  # dashboard direct
```

## After the auth key has done its job

The auth key is single-purpose: get the container into the tailnet on first start. After the daemon authenticates, it caches a long-lived node key in `data/tailscale/`. You can:

- **Rotate the auth key in the Tailscale admin** — won't disconnect this device.
- **Delete the auth key entirely** — won't disconnect this device.
- **Wipe the key from .env** — set `TS_AUTHKEY=` to empty. The container still works because of the cached node key.

If `data/tailscale/` is ever deleted, the container needs a fresh auth key to rejoin.

## Disabling

```bash
ods disable tailscale
# Or: Extensions → Tailscale → Disable
```

This stops the container but leaves the cached node key in `data/tailscale/`. Re-enabling rejoins without a new auth key.

To fully remove from the tailnet, also delete the device entry in the Tailscale admin (`https://login.tailscale.com/admin/machines`).

## API surface

### `GET /api/tailscale/status`

Auth: Bearer (dashboard).

Three shapes, always 200:

| Container | TS daemon | Response |
|---|---|---|
| Not running | — | `{"running": false}` |
| Running | Not authenticated | `{"running": true, "authenticated": false, "reason": "..."}` |
| Running | Authenticated | `{"running": true, "authenticated": true, "self": {hostname, dns_name, ips, online}, "magic_dns_suffix": "...", "tailnet_name": "..."}` |

Errors (5xx) are reserved for "the docker daemon itself broke" — never for "the extension isn't enabled."

## Caveats

- **Linux only in v1.** Host networking on macOS / Windows Docker Desktop doesn't share the host's network namespace the same way. Both work in some configurations with `TS_USERSPACE=true` and `tailscale serve`, but that's substantially more complex than this PR ships. Documented as a follow-up.
- **No HTTPS yet.** Tailscale's HTTPS feature (auto-issued certs from `<hostname>.<tailnet>.ts.net`) requires `tailscale cert` + a proxy. Future PR.
- **No Funnel (public internet exposure).** Tailscale Funnel can expose a tailnet service to the public internet via `*.ts.net`. We don't enable this by default — it would defeat the "identity-based access" property. Operator opt-in if they want it.
- **The container needs `NET_ADMIN`, `NET_RAW`, and `/dev/net/tun`.** These are unusual for Docker containers. They're necessary for any WireGuard-based VPN. The container runs as root; this is the Tailscale-published image's standard pattern.

## Troubleshooting

### `tailscale status` says "Logged out"

Either:
- `TS_AUTHKEY` is empty or expired in `.env` → mint a fresh key and `ods restart tailscale`
- The auth key was single-use and already consumed by a different container → mint a reusable key

### Device joined but unreachable from other tailnet members

- Check your tailnet ACLs (`https://login.tailscale.com/admin/acls`). The default ACL allows all-to-all; if you've restricted it and use `--advertise-tags=tag:ods`, make sure `tag:ods` is defined and allowed inbound.
- Check the receiving side actually has the Tailscale client running: `tailscale status` on the other device should show your ods node.

### Network is sluggish

Tailscale uses DERP relays as a fallback when direct UDP isn't possible. Behind a strict NAT or carrier-grade NAT, you'll go through a relay (typically <50ms in the US but variable). Run `tailscale netcheck` for a diagnosis.
