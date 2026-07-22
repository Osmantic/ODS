# OAuth Provider Setup

ODS's OAuth passthrough removes the copy-paste-code step: a provider
redirect lands on `/api/oauth/callback`, dashboard-api captures the short-lived
code, and Hermes can finish the skill setup.

Provider registration is the separate preflight step. Public ODS
releases do not commit shared OAuth client secrets to git. A distributor can
ship a private credential bundle, and operators can always bring their own
credentials.

## Provider Registry

The provider registry lives at:

```text
extensions/services/hermes/oauth-providers.json
```

It records provider IDs, skill IDs, expected credential filenames, preferred
flows, redirect URI patterns, and provider-verification notes. It is metadata
only; it contains no client secrets.

Dashboard API exposes a secret-free readiness endpoint:

```bash
curl -H "Authorization: Bearer $DASHBOARD_API_KEY" \
  http://127.0.0.1:3002/api/oauth/providers
```

The endpoint reports whether each provider has a credential file in one of the
configured search roots. It never returns credential contents.

## Credential Search Roots

By default dashboard-api checks:

```text
data/hermes/
data/hermes/credentials/
data/persona/oauth/
```

Override with `ODS_OAUTH_CREDENTIAL_DIRS` using the platform path separator
if a fork or appliance stores credentials somewhere else.

## Private Distribution Bundle

A downstream distributor can provide credentials out of band, for example:

```text
credentials/oauth/
  google_client_secret.json
  spotify_client.json
  github_oauth.json
```

Copy the relevant files into `data/hermes/` or `data/hermes/credentials/` on
the installed system, then make sure Hermes can read them. On Linux installs
Hermes usually owns `data/hermes/` as uid `10000`, so preserve owner-only file
modes and ownership.

## Bring Your Own Credentials

Operators who prefer their own OAuth app should create provider credentials
with redirect URIs matching their install. Common local patterns are:

```text
http://ods.local:3002/api/oauth/callback
http://localhost:3002/api/oauth/callback
http://127.0.0.1:3002/api/oauth/callback
```

If the device uses a custom `ODS_DEVICE_NAME`, add the matching
`http://<device>.local:3002/api/oauth/callback` URI in the provider console.

## Provider Notes

- Google Workspace scopes such as Gmail and Drive can require verification
  before the consent screen feels polished. Unverified apps may still work for
  testing, but the warning is bad user experience.
- Spotify supports PKCE for public clients. Prefer PKCE when the skill supports
  it so local appliances do not need a shared client secret.
- GitHub skills should prefer device flow when possible. It avoids shipping a
  client secret and fits local appliances well.

## Callback State Validation

`/api/oauth/callback` is a public redirect target, so it cannot rely on session
cookies or bearer auth. It relies on a server-issued `state` nonce instead:

1. The agent calls `POST /api/oauth/init` (authenticated with the dashboard
   API key) with `{"skill_id": "<skill>", "return_url": "/talk"}`.
2. Dashboard-api generates a high-entropy nonce, persists
   `{nonce, skill_id, return_url, created_at, ttl}` under
   `data/persona/oauth-nonces/<nonce>.json` (mode 0600), and returns the
   nonce as `state`.
3. The agent embeds that nonce as the OAuth `state` parameter when it
   builds the auth URL.
4. When the provider redirects to `/api/oauth/callback`, dashboard-api
   looks up the nonce, rejects any callback whose state is missing,
   malformed, unknown, expired, or already consumed, and only on a
   valid nonce writes `data/persona/oauth_callback.json`.
5. Nonces are single-use — consumed (deleted) before the callback file
   is written, so a race between two callbacks with the same state
   resolves to a single winner.
6. `skill_id` and `return_url` are bound at init time. The callback
   endpoint never accepts either as a query param, so an attacker who
   crafts a callback URL cannot influence which skill the agent later
   finalises against.

Operators generally don't need to interact with this directly — Hermes'
skill setup scripts handle the init handshake — but it's the reason a
callback appearing at `data/persona/oauth_callback.json` can be trusted.

## Safety Rules

- Do not commit real OAuth client secrets to the public repository.
- Do not print credential contents in dashboard-api responses, logs, support
  bundles, or docs.
- Keep `/api/oauth/callback` public because providers must redirect to it.
  The callback endpoint's protection is the server-issued state nonce
  (see Callback State Validation above), not authentication.
- Keep readiness/status endpoints auth-gated.
- Keep `/api/oauth/init` auth-gated — anyone who can mint nonces can
  potentially set up a phishing flow, so treat it like any other
  authenticated write endpoint.
