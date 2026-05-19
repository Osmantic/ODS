# Hermes SSO — magic-link gating in front of the Hermes Agent

Dream Server's `hermes-proxy` extension is a Caddy reverse proxy that fronts the [Hermes Agent container](HERMES.md) and gates access on Dream Server's magic-link auth.

When this extension is enabled:

- Hermes itself binds **internal-only** (no host port).
- The proxy binds the LAN-facing port (default `9120`) — that's what users browse to.
- Every request to the proxy is verified via `forward_auth` against dashboard-api's `/api/auth/verify-session` endpoint — which HMAC-validates the `dream-session` cookie's signature against `DREAM_SESSION_SECRET`.
- Verified (HTTP 200 from the verify endpoint) → traffic is forwarded to `dream-hermes:9119`. Hermes's own [per-process session token model](HERMES.md#security-posture) then handles per-request `/api/` auth.
- Not verified (HTTP 401 — missing cookie, bad signature, or expired) → 303 redirect to a static "you need an invite" page.

## Why this design

After reading [upstream's web_server.py at our pinned SHA](https://github.com/NousResearch/hermes-agent/blob/dd0923bb89ed2dd56f82cb63656a1323f6f42e6f/hermes_cli/web_server.py), Hermes's auth is **per-PROCESS, not per-user**. The session token is `secrets.token_urlsafe(32)` generated at server start and baked into the SPA HTML — no env-var to pre-seed, no login flow, no user concept.

Hermes **does** support per-user isolation via [profiles](https://github.com/NousResearch/hermes-agent/blob/dd0923bb89ed2dd56f82cb63656a1323f6f42e6f/hermes_cli/profiles.py) (separate `HERMES_HOME/profiles/<name>/`), but a profile is bound at process launch — one Hermes process = one profile. There's no in-process profile switching.

This extension does NOT try to give you real multi-user. It gives you:

| Property | Achieved? |
|---|---|
| Magic-link-authed gateway | ✅ |
| Anyone with a valid invite can reach Hermes | ✅ |
| Anyone without a valid invite gets bounced | ✅ |
| Mom's memories / skills / sessions isolated from Dad's | ❌ — shared |
| The proxy knows WHO is logged in | ❌ — only that *someone* has a valid invite |

If you need per-user isolation, the path is to run **one Hermes container per user** (each with its own profile), and have the proxy route based on the redeemed user's identity. That's [Option B](#future-option-b--per-user-hermes) below — out of scope for v1.

## Setup

```bash
# 1. Enable Hermes (the agent itself)
dream enable hermes

# 2. Enable the auth proxy (this extension)
dream enable hermes-proxy

# 3. Generate an invite from the dashboard
#    → Browse to http://<device>:3001/invites
#    → "New invite" → scope: chat or all → Generate
#    → Save the QR / URL

# 4. Recipient scans the QR on their phone
#    → Lands on the dream-proxy at <device>.local/auth/magic-link/<token>
#    → Redemption sets the HMAC-signed dream-session cookie on <device>.local
#    → 302 redirect to /chat (same origin → cookie travels along)
#    → They now have a valid cookie for any same-host port too (cookies
#      are scoped by host, not port — so <device>.local:9120 will receive
#      it as well)

# 5. Recipient browses to http://<device>.local:9120
#    → Proxy forward_auths to dashboard-api/api/auth/verify-session
#    → Signature check passes → forward to Hermes
#    → Hermes serves the SPA → they chat
```

If the recipient hasn't yet redeemed an invite, step 5 lands them on the "you need an invite" page with instructions.

## Architecture

```
Phone / laptop
   │
   ▼  http://<device>.local:9120
┌──────────────────────────────────────────┐
│  dream-hermes-proxy  (Caddy, ~50MB)      │
│                                          │
│  Caddyfile match rules:                  │
│    /health, /favicon.ico → respond       │
│    /auth/required*       → static files  │
│    everything else        → forward_auth │
│                                          │
│  forward_auth sub-request:               │
│    GET /api/auth/verify-session →        │
│      dashboard-api:3002                  │
│        2xx → reverse_proxy dream-hermes  │
│        401 → 303 to /auth/required       │
└──────────┬───────────────────────────────┘
           │
           ▼  internal Docker bridge network only
┌──────────────────────────────────────────┐
│  dream-hermes  (NousResearch image)      │
│  - exposes :9119 internally              │
│  - DOES NOT bind a host port             │
│  - serves its React SPA + /api/*         │
│  - its own X-Hermes-Session-Token gates  │
│    /api/* requests per-request           │
└──────────────────────────────────────────┘
           │
           ▼  OpenAI-compatible API
       llama-server (existing)
```

## What gets verified

The `dream-session` cookie is set by the dashboard-api's magic-link redemption (`routers/magic_link.py`) and signed with HMAC-SHA256 against `DREAM_SESSION_SECRET` (see `session_signer.py`). The cookie:

- Is `HttpOnly` (JS can't read it)
- Has `SameSite=Lax` (sent on top-level navigation cross-origin GETs, blocked on background cross-site POSTs)
- Is `Secure` when the redemption host was reached over HTTPS
- Has `Max-Age = 12h` from redemption
- Carries `<random-id>.<expiry-epoch>.<hmac-signature>` — the signature is what gates validity, not presence

On every request the proxy issues a sub-request to `dashboard-api/api/auth/verify-session`, which:

1. Splits the cookie value on `.`
2. Recomputes the HMAC over `<random-id>.<expiry-epoch>` and constant-time-compares it (`hmac.compare_digest`) against the claimed signature
3. Checks the embedded expiry hasn't passed

Any failure (missing cookie, tampered signature, expired timestamp, missing server-side secret) returns a single byte-identical 401 response so an attacker can't probe which step failed.

What this catches:
- Forged cookies (any value not signed with the secret fails the HMAC check)
- Expired cookies (server-side expiry, independent of the browser's `Max-Age`)
- Tampered cookies (changing the `<random-id>` or extending `<expiry-epoch>` invalidates the signature)

What this does NOT do:
- Identify which user is behind a request — the cookie is opaque (the random-id is not a username)
- Track per-cookie revocation. Today's only revocation mechanism is rotating `DREAM_SESSION_SECRET`, which invalidates every issued cookie. A future PR could add a revocation list keyed on the random-id; the cookie format reserves space for it.

For the Dream Server trust model (single home, trusted LAN, family-scale users), this gives real signature-based gating without a session store. The proxy explicitly says "**gating**, not identification."

## Known limitations

1. **No real multi-user.** All authed users share one Hermes — same memories, skills, persona, sessions. Mom can see Dad's chats and vice-versa. Treat Hermes as "the family's agent."

2. **Stolen cookies are valid until expiry or secret rotation.** The cookie is signed, but if it's exfiltrated (malicious browser extension, leaked screenshot, etc.) the attacker can use it until the 12h expiry passes or the operator rotates `DREAM_SESSION_SECRET`. There's no per-cookie revocation today. The signed format reserves the random-id field for a future revocation list.

3. **No per-request user identification.** The proxy doesn't add an `X-Dream-User` header to forwarded requests. Hermes can't know "this request is from Alice" — only "this request is from someone with a valid signed cookie."

4. **The cookie's `dream-target-user` field is ignored by the proxy.** Magic-link redemption sets a second cookie naming the target username, but the proxy doesn't surface it to Hermes (there's no Hermes-side hook to consume it).

5. **Direct access to Hermes is now blocked.** Anyone who was reaching Hermes at `:9119` before this extension lands needs to switch to `:9120` (the proxy port). If they want raw direct access for testing, they can `docker exec dream-hermes` or temporarily re-add a `ports:` binding to the Hermes compose.

6. **`DREAM_SESSION_SECRET` must be configured.** With no secret set, `verify-session` returns 401 for every request (and `issue()` raises during magic-link redemption) — the proxy gate effectively becomes "nobody passes." Set a 32+-byte random value in `.env` before enabling `hermes-proxy`.

## Future: Option B — per-user Hermes

If/when real multi-user becomes a felt need, the path is:

1. dashboard-api dynamically spawns a Hermes container per magic-link `target_username` (each with `HERMES_HOME=/opt/data/profiles/<username>`)
2. The Hermes auth proxy gains a routing layer — reads the `dream-target-user` cookie set during redemption, maps it to the per-user container's address, forwards there.
3. Lifecycle management: idle-timeout to stop unused containers; cold-start when a user returns.

Roughly 2-3 PRs of work and meaningful resource cost (each Hermes container is ~3GB image + ~1GB idle RAM with chromium / playwright loaded). Not worth it until a family member specifically asks for "my own Hermes."

## Disabling the proxy

```bash
dream disable hermes-proxy

# To restore direct Hermes access, re-add a ports binding to
# extensions/services/hermes/compose.yaml:
#   ports:
#     - "${BIND_ADDRESS:-127.0.0.1}:${HERMES_PORT:-9119}:9119"
# (then `dream restart hermes`)
```

## Bump history

| Date | Pinned Caddy | Notes |
|---|---|---|
| 2026-05-12 | `caddy:2.8.4-alpine` | Initial integration. |
| 2026-05-15 | `caddy:2.11.3-alpine` | Updated Hermes Proxy image to the current Caddy 2.x stable pin. |
