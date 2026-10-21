# Tailscale (remote access)

ODS includes an optional Tailscale extension that joins the device to your private tailnet. Joining provides a tailnet IP/DNS name; it does not publish services that listen only on loopback. Portal's default listeners are loopback-only.

For private Portal access from another computer, use the SSH forward below. It keeps the default service bindings and Portal sign-in, without requiring `ods-proxy` or a public endpoint.

## Why Tailscale (vs. exposing ports / port-forwarding / Cloudflare Tunnel)

- **Zero NAT / port-forwarding config.** You don't touch your router. Tailscale's mesh handles NAT traversal.
- **Identity-based access.** Tailnet access rules control which devices can reach the ODS host. SSH and Portal still enforce their own authentication.
- **End-to-end encrypted.** WireGuard under the hood.
- **No public endpoint required.** The SSH route below needs no public HTTP listener. Tailscale does not restrict services separately published on other interfaces.

Tradeoff: every device that needs to reach ODS has to install the Tailscale client. For "me and my family" that's fine. For "publish this to anyone on the internet," you'd want Cloudflare Tunnel + auth instead — out of scope for this extension.

## Architecture

```text
Client browser: http://127.0.0.1:13011/
  -> client loopback SSH listener
  -> SSH to the actual ODS host over Tailscale
  -> ODS host 127.0.0.1:3011
  -> Portal (dashboard sign-in required)
```

The Linux Tailscale container uses `network_mode: host`. Incoming tailnet traffic still needs a service listening on the destination address; it cannot directly reach the host's loopback-only ports.

## Portal listeners and private access

The default published ports have different roles:

| Host port | Purpose | Binding and authentication |
|---|---|---|
| `3001` (`DASHBOARD_PORT`) | Local-owner Portal | Always published on `127.0.0.1`, even when `BIND_ADDRESS` changes. Local requests can use owner access without sign-in. Do not forward this listener for remote use. |
| `3011` (`DASHBOARD_REMOTE_PORT`) | Portal with sign-in | Uses `BIND_ADDRESS`, default `127.0.0.1`. Requires dashboard sign-in even through a loopback SSH forward. |
| `3000` (`WEBUI_PORT`) | Optional Open WebUI | Separate application; uses `BIND_ADDRESS`, default `127.0.0.1`. It is not Portal. |
| `3002` (`DASHBOARD_API_PORT`) | Dashboard API | Published on loopback; keep it private. Portal proxies its API requests. |

On a client with Tailscale and working SSH access to the **actual ODS host**, run:

```bash
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:13011:127.0.0.1:3011 user@ods.tail-abcde.ts.net
```

Replace the SSH account and hostname with yours. If this installation overrides `DASHBOARD_REMOTE_PORT`, replace the destination `3011` with that port. SSH must allow local forwarding. This command assumes the default loopback service binding; it does not enable SSH or change tailnet access rules. Leave the terminal open, then browse to **`http://127.0.0.1:13011/`** on that client. Stop the forward with Ctrl+C when finished. Portal is served at `/`; changing the path to `/pixel` cannot fix a refused TCP connection.

Sign in with your dashboard password. To initialize or recover it, run `ods dashboard-login` as the installation owner **on the same ODS host**. Append the printed `#ods-login=...` fragment to `http://127.0.0.1:13011/`, then open that URL privately to choose a password. The link works once and expires in ten minutes; do not share it in logs, screenshots, or support messages. The forward must terminate on that ODS host, not a different laptop running a tunnel.

### If you already use `ods-proxy`

The proxy is optional for this route. Its published listener uses **`ODS_PROXY_BIND`** (default `0.0.0.0`) and **`ODS_PROXY_PORT`** (default `80`), independently of `BIND_ADDRESS`. Enabling it can expose HTTP on other host interfaces; joining Tailscale does not make that listener tailnet-only. Keep the default service bindings for the SSH route above.

The bundled proxy routes by **hostname**, for example `dashboard.<ODS_DEVICE_NAME>.local` to Portal's sign-in listener and `chat.<ODS_DEVICE_NAME>.local` to Open WebUI. Clients need those names to resolve to the intended host. The bare Tailscale MagicDNS name does not automatically acquire these routes, and the proxy does not route `/chat`, `/api/*`, and `/auth/*` by path on that name. See [ODS proxy](ODS-PROXY.md) for its hostname layout.

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

From the client, with the SSH forward above still running:

```bash
curl -i http://127.0.0.1:13011/api/status
# Without a dashboard session, expect HTTP 401 and X-ODS-Sign-In: required.
# Then open http://127.0.0.1:13011/ in the browser and sign in.
```

Loading the page alone does not prove authenticated API or Pixel readiness. After signing in, verify status and send a Portal message. A direct request to the tailnet IP on `3001` should remain unreachable with the default configuration; that is not a Tailscale failure.

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
- **The bundled proxy is HTTP-only.** The SSH route above encrypts the connection between computers. [Tailscale Serve](https://tailscale.com/kb/1312/serve) is a separate option for HTTPS within a tailnet; this extension does not configure it. Any remote Portal proxy must use the sign-in listener (`3011` by default).
- **No Funnel (public internet exposure).** Tailscale Funnel can expose a tailnet service to the public internet via `*.ts.net`. We don't enable this by default — it would defeat the "identity-based access" property. Operator opt-in if they want it.
- **The container needs `NET_ADMIN`, `NET_RAW`, and `/dev/net/tun`.** These are unusual for Docker containers. They're necessary for any WireGuard-based VPN. The container runs as root; this is the Tailscale-published image's standard pattern.

## Troubleshooting

### `tailscale status` says "Logged out"

Either:
- `TS_AUTHKEY` is empty or expired in `.env` → mint a fresh key and `ods restart tailscale`
- The auth key was single-use and already consumed by a different container → mint a reusable key

### Device joined but unreachable from other tailnet members

- Confirm the hostname belongs to the host running this ODS installation, and check `ods status` there. Healthy Pixel edge or model-relay containers alone do not prove that Portal is listening.
- A refused direct connection to `3001` is expected: it is the local-owner listener. Use the SSH forward to the sign-in listener and check the installation's `DASHBOARD_REMOTE_PORT` if it differs from `3011`.
- If SSH works but the forwarded request is refused, confirm the destination listener is available on the ODS host. Keep `BIND_ADDRESS` unchanged while diagnosing; a broad bind change is not required for the default SSH route.
- Check your tailnet ACLs (`https://login.tailscale.com/admin/acls`). The default ACL allows all-to-all; if you've restricted it and use `--advertise-tags=tag:ods`, make sure `tag:ods` is defined and allowed inbound.
- Check the receiving side actually has the Tailscale client running: `tailscale status` on the other device should show your ods node.

### Network is sluggish

Tailscale uses DERP relays as a fallback when direct UDP isn't possible. Behind a strict NAT or carrier-grade NAT, you'll go through a relay (typically <50ms in the US but variable). Run `tailscale netcheck` for a diagnosis.
