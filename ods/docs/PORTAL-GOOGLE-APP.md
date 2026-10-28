# Google sign-in provided by ODS

The intended ordinary journey is **Connect Google → choose an account → grant
access**. Registering a Google Cloud project and downloading a client JSON file
belongs to the ODS maintainers' release work. It is not an ordinary user's setup
step. Owner-provided clients remain an explicit Advanced option.

## Implemented connection journey

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
- The authenticated Root → Edge/host → dashboard API path carries that metadata
  into `catalog[].auth`. The browser sends `authMode: "managed"` with the client
  field omitted. Client documents and configuration paths in that request are
  rejected. The exact older owner-client contract is still accepted.
- Settings starts with permissions and **Continue to Google**. Uploading a
  Desktop client is available only after choosing Advanced. Missing deployment
  configuration disables standard sign-in and explains that it needs an ODS
  update; it does not silently ask every owner to create a Google project.
- The consent journal and connected account retain their own `authMode`, so
  repair instructions reflect the app actually used rather than today's global
  configuration. Older records default to Advanced. Existing Sync/Disconnect
  actions continue to use the broker's lifecycle.

Desktop applications use public-client credentials and cannot keep a distributed
client secret confidential. PKCE and the existing fixed loopback flow remain
required. Google's [Desktop authorization documentation](https://developers.google.com/identity/protocols/oauth2/native-app)
describes the supported flow.

## Remaining release work

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

Controlled-provider HTTP journeys exercise both managed and legacy sign-in,
including callback rejection, one exchange, activation and withdrawal. They
simulate Google and privileged service boundaries. A separate real personal
Desktop client established a read-only connection locally; that client is not
the official ODS registration and is not distributed. Neither result qualifies
the complete released sign-in experience or replaces provider review.
