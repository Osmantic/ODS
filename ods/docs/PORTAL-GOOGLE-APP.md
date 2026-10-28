# Managed Google application foundation

The intended ordinary journey is **Connect Google → choose an account → grant
access**. Registering a Google Cloud project and downloading a client JSON file
belongs to the ODS maintainers' release work. It is not an ordinary user's setup
step. Owner-provided clients remain an Advanced fallback.

## Implemented internal contract

- Root accepts `authMode: "managed"` without any browser-supplied client or
  configuration path. It resolves `/etc/ods/portal-google-app.json` inside the
  existing Root lock, before replacing consent or preparing services.
- That private Root-owned file has the exact envelope `kind:
  "ods-managed-google-app"`, `schemaVersion: 1`, and `client` containing the
  maintainer-supplied registered Desktop client document. The loader refuses
  unsafe ownership, writable ancestors, symlinks, hardlinks, oversized/ambiguous
  JSON and unsupported client types/endpoints. No actual client is included in
  this repository, and no personal client is promoted to an ODS application.
- The existing Source Broker handles PKCE, consent, exchange, private tokens,
  refresh and revocation. Managed consent requests an account chooser. Explicit
  `authMode: "advanced"` and the exact legacy owner-client contract remain
  compatible; neither is an automatic fallback for missing deployment config.
- The credential-free projection distinguishes `configured`, `not-configured`
  and `unavailable`, with `mode: "managed"` and Advanced availability. The
  catalog can join that Root-supplied projection without reading credentials as
  the host/browser identity. Local configuration never claims Google approval.

Desktop applications use public-client credentials and cannot keep a distributed
client secret confidential. PKCE and the existing fixed loopback flow remain
required. Google's [Desktop authorization documentation](https://developers.google.com/identity/protocols/oauth2/native-app)
describes the supported flow.

## Remaining release and public integration work

The official ODS application still needs registration, API enablement, branding
and the applicable scope verification. Gmail read/draft scopes are restricted;
the release's actual handling and sharing of data must be reviewed against
[Google's scope requirements](https://developers.google.com/workspace/gmail/api/auth/scopes)
and [OAuth policies](https://developers.google.com/identity/protocols/oauth2/policies).
The code does not register or pay for an application, certify verification,
bypass an unverified-app screen or guarantee a provider will accept a client.

A reviewed release/deployment process must provision the client with appropriate
source custody; Root ownership alone is not proof of publisher provenance. This
foundation neither provisions nor deletes that operator-managed application
configuration. It contains no per-user tokens. Personal sign-ins remain owned
by the broker's separate cleanup lifecycle.

Authenticated host/API/Settings wiring remains unfinished. The current public
transport deliberately keeps its legacy catalog shape until it explicitly
carries the new Root configuration state. The final UI must make managed login
the normal path, put owner-client setup under Advanced, and explain an
unconfigured release without asking ordinary users to create Google projects.
Controlled-provider tests qualify the internal trust seam, not a real official
Google app or the complete released one-click experience.
