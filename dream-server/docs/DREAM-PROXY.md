# Dream Server reverse proxy (`dream-proxy`)

The single LAN-facing entry that makes `http://<device>.local` (no port) actually work, with per-service subdomains for chat, dashboard, auth, and hermes.

Without this extension, Dream Server's services bind to `127.0.0.1` by default — they're reachable from the host but not from another device on the LAN. A phone scanning a "browse to `http://dream.local`" QR code hits port 80 on the device, finds nothing, gives up. The dashboard's promise of `<device>.local` as a one-tap entry was broken before this extension.

With it, port 80 becomes the single entry point. Caddy answers each subdomain on the device's mDNS hostname and forwards to the right backend, **root-mounted** — no subpath gymnastics.

```
<device>.local           → 302 → chat.<device>.local
chat.<device>.local      → Open WebUI            (port 3000)
dashboard.<device>.local → Dream Dashboard       (port 3001)
auth.<device>.local      → dashboard-api         (port 3002, magic-link redemption)
api.<device>.local       → dashboard-api         (port 3002, admin /api/*)
hermes.<device>.local    → hermes-proxy          (port 9120, when enabled)
/health                  → Caddy itself ("ok") — served on every host for easy probing
```

Each subdomain needs a LAN name record pointing at the Dream Server device. The companion `dream-mdns` service handles this automatically when enabled; until then, add equivalent DNS/hosts records for the subdomains you want to use. Other Dream services keep their loopback bindings. The proxy is the only thing that opens up to the LAN.

## Why host-based and not path-based

An earlier draft routed paths off the bare hostname (`<device>.local/chat`, `<device>.local/api/`, etc.). That broke because each backend was already coded to live at the root:

- Open WebUI's static assets and websocket endpoints assume root mounting; mounting under `/chat` produced broken root-relative paths and dead websockets.
- React Router base-href games would have been required for the dashboard.
- Open WebUI's OAuth callback URLs ignore proxy subpath rewriting and would have leaked back to the bare hostname.

Host-based routing sidesteps all of it. Every backend stays at `/`, the proxy just terminates the Host header.

## Cookie scope (and why this matters for magic links)

`dashboard-api` sets the `dream-session` cookie on `auth.<device>.local` during magic-link redemption with `Domain=<device>.local`. That makes the cookie visible to every subdomain: chat, hermes, dashboard, api. So a single redemption authenticates the user across all of them, even though each subdomain is a different origin.

The cookie's Domain is controlled by the `DREAM_COOKIE_DOMAIN` env var in dashboard-api. Default empty (host-only); the installer sets it to `<device>.local` (the value of `DREAM_DEVICE_NAME` + `.local`) so SSO works out of the box.

## When to enable it

- **Yes** if you want to reach Dream Server from a phone / laptop on the same network at `http://<device>.local`.
- **Yes** if you're using Tailscale (PR-12) — the proxy becomes the single endpoint exposed on the tailnet too.
- **No** if Dream Server is single-user / localhost-only — you save a small process and a port binding.

```bash
dream enable dream-proxy
# Test (substitute <device> with your DREAM_DEVICE_NAME, default "dream"):
curl http://chat.<device>.local/         # → Open WebUI
curl http://dashboard.<device>.local/    # → Dream Dashboard
curl http://auth.<device>.local/health   # → ok (Caddy)
```

## Prerequisites

For the bare URL to actually load anything, two host-level conditions must hold:

1. **`DREAM_PROXY_BIND=0.0.0.0`** in `.env`, or left unset so the proxy's default applies. The proxy listens on the LAN interface; with `DREAM_PROXY_BIND=127.0.0.1`, the LAN can't reach it. Do not set global `BIND_ADDRESS=0.0.0.0` just for this — that exposes every service instead of only the proxy.
2. **mDNS, DNS, or hosts-file records publish the per-service subdomains.** The companion `dream-mdns` service handles this automatically when enabled; without it, create equivalent records manually.

The installer's first-boot flow handles both. If you're not using the installer, leave `DREAM_PROXY_BIND` at its default or set it explicitly, then run `dream enable dream-proxy` manually.

## Security posture

**The proxy is the trusted gate.** Behind it, each service's own auth applies:

- `dashboard-api`: API key (`DASHBOARD_API_KEY`)
- Open WebUI: its own auth (`WEBUI_AUTH=true` by default — users sign up / sign in)
- Dashboard SPA: the React app shows admin features only when the API call succeeds
- `hermes-proxy`: Caddy `forward_auth` against `dashboard-api/api/auth/verify-session` (signed-cookie check)

The proxy itself adds NO auth layer. Adding one here would duplicate without strengthening.

**Trust model:**

- Trusted LAN: a home network where everyone on the network is in the household. Exposing the proxy on the LAN is fine.
- Tailscale: also fine — Tailscale's identity-based access is its own auth layer.
- Public internet: ❌ **NEVER**. Don't publish port 80 to the public internet without an additional auth/TLS layer.

## TLS

HTTP only in v1. Adding HTTPS needs one of:

1. **Tailscale-issued certs** — `tailscale cert <hostname>.<tailnet>.ts.net` produces a real Let's-Encrypt cert; Caddy can serve it directly. Documented as a follow-up.
2. **Self-signed cert + device trust** — operator generates a cert, distributes the CA to family devices.
3. **Caddy's auto-https for public domains** — only works if you have a real DNS name. Not the `.local` case.

For now, plain HTTP on the trusted LAN. The cookie-issuing flows that set `Secure=` honor the request scheme — they'll set the Secure flag once TLS is in front.

## How to bypass the proxy

`dream disable dream-proxy` stops the container. Each backend service goes back to being only reachable on its individual port (`<host-ip>:3000`, `<host-ip>:3001`, etc.) — and only if `BIND_ADDRESS=0.0.0.0` is set globally. Otherwise they stay loopback.

If you want LAN access to a single specific service without the proxy, add a `ports:` binding to that service's compose file (or set `BIND_ADDRESS=0.0.0.0` globally — but that exposes ALL services, the security tradeoff this whole extension was designed to avoid).

## Bump history

| Date | Pinned Caddy | Notes |
|---|---|---|
| 2026-05-12 | `caddy:2.8.4-alpine` | Initial integration. HTTP only; TLS deferred. |
| 2026-05-12 | `caddy:2.8.4-alpine` | Switched from path-based (`/chat`, `/api/*`) to host-based (`chat.<device>.local`, etc.) routing after audit on subpath issues. Cookie domain via `DREAM_COOKIE_DOMAIN`. |
| 2026-05-15 | `caddy:2.11.3-alpine` | Updated Dream Proxy image to the current Caddy 2.x stable pin. |
