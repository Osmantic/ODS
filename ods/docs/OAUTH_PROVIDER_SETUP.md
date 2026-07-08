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

## Safety Rules

- Do not commit real OAuth client secrets to the public repository.
- Do not print credential contents in dashboard-api responses, logs, support
  bundles, or docs.
- Keep `/api/oauth/callback` public because providers must redirect to it.
- Keep readiness/status endpoints auth-gated.
