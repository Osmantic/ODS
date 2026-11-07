# Google sign-in provided by ODS

The intended ordinary journey is **Connect Google → choose an account → grant
access**. Registering a Google Cloud project and downloading a client JSON file
belongs to the ODS maintainers' release work. It is not an ordinary user's setup
step. Owner-provided clients remain an explicit Advanced option.

## Maintainer publication setup

Use a separate production Google Cloud project named **ODS Portal**, with
maintainer ownership and support contacts that can be kept current. A local QA
project and its personal Desktop client are not the distributed ODS application.
The display name can be chosen; home-page and privacy-policy addresses must be
real, publicly accessible and on a domain the maintainers can verify.

The repository already uses `osmantic.com` for its project site and
`install.osmantic.com` for installation. Subject to control of that domain, it is
the proposed publication domain; buying another domain is not a prerequisite.
Registering a Cloud project does not verify domain ownership or publish an app.

The separate maintainer project now has an **ODS Portal** identity, a selected
support/developer contact and a **Testing** audience. This is initial branding,
not domain verification, approved scopes or a distributed Desktop client.
The live contact and project identifiers stay in release configuration rather
than application source. The privacy link found beside the site's contact form
is Formspree's policy, not a Portal Google-data policy. A
[maintainer notice draft](PORTAL-GOOGLE-DATA-NOTICE-DRAFT.md) records the actual
local/cloud data paths and the publication details still requiring review.

Project names, IDs and numbers identify the application; they are not user
tokens or a grant to read mail. The project creator is its initial administrator,
so production ownership should be shared with the responsible maintainers and
use maintained project support contacts. Each installation retains its own
private sign-in. A user's connection does not make that user's email content
available in the maintainer's Google account.

The shared application is subject to Google's OAuth audience limits and API
quotas. A Testing audience is limited to 100 listed test users, and its offline
grants normally expire after seven days when requesting Gmail/Calendar access.
Production scope approval and API capacity are separate requirements; neither
is established by creating the project. Review the actual quota/billing
configuration before distribution, and do not attach a personal billing account
as an implicit setup step. See Google's
[audience limits](https://support.google.com/cloud/answer/15549945?hl=en) and
[Gmail API quotas](https://developers.google.com/workspace/gmail/api/reference/quota).

Prepare this once for the distributed client:

1. Establish maintainers' access to the production project and verify the
   publication domain in Search Console with an account attached to the project.
2. Publish a Portal page describing Google integration, linked to a privacy
   policy on the same domain. Describe actual local storage, deletion,
   permissions, background sync and optional sharing with a selected cloud
   model. Proposed pages are not evidence that this work is published.
3. Configure the **ODS Portal** branding and maintained support/developer
   contacts. Enable the APIs needed by the release and register its Desktop
   client for the existing loopback/PKCE flow.
4. Request only permissions implemented and being released. Gmail read access
   is already restricted; read-only does not eliminate scope verification.
   Review any cloud-model/server data path for the applicable security
   assessment requirements before claiming public readiness.
5. Submit the actual consent and feature demonstration for Google's review.
   Approval of a project name, brand or domain does not itself approve Gmail
   scopes. Keep development and production consent separate.
6. Provision the reviewed production client through the release path described
   below and verify a clean installation's normal **Connect Google** journey.
   No per-user project, API enablement or JSON download belongs in that journey.

The maintainers' current project ID, verification records and deployment client
belong to the release configuration, not hard-coded browser requests. No
production client or token is created by this document. See Google's
[brand verification requirements](https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification)
and [restricted-scope review](https://developers.google.com/identity/protocols/oauth2/production-readiness/restricted-scope-verification).

## Capacity and operating costs

The 100-user Testing/unverified-app cap is not a paid user tier. With the
requested sensitive/restricted scopes approved, that cap no longer applies to
those scopes. Approval does not remove API quotas, which are enforced both per
project and per user. Separate local installations using the same distributed
client still share the project's aggregate API allowance.

The intended architecture connects each installation directly to Google and
keeps its private grant locally; it does not require an ODS mail-hosting server.
This reduces central infrastructure without promising unlimited provider usage.
Google now documents usage thresholds and future overage charging for large
Gmail/Calendar workloads. Review their current
[Gmail](https://developers.google.com/workspace/gmail/api/reference/quota) and
[Calendar](https://developers.google.com/workspace/calendar/api/guides/quota)
limits before public distribution or a capacity commitment.

Restricted-scope security review is separate from API consumption. Google
does not charge a security-assessment fee itself; an independent assessor may
charge, and eligibility for a free assessment depends on Google's assigned
tier. Review the actual local storage and optional remote-model data paths
before concluding which assessment applies. Local token custody alone is not
proof of an exemption. See the
[verification and assessment FAQ](https://support.google.com/cloud/answer/13463817?hl=en).

An owner-provided client remains an Advanced deployment alternative with its
own project setup and quotas. That extra manual work is not the standard
ordinary-user journey. Neither mode establishes free, unlimited Google access
at arbitrary scale.

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

## Reconnecting an existing account

Before replacing a grant, Root records an interrupted-exchange barrier, stops
the refresh/direct producers and active action/reconciliation instances, and
withdraws the old Google record. Regenerable Google caches/cursors are cleared;
independent social data and the live OAuth handoff remain. Old action queues and
results are retained in a bounded private archive and never replayed automatically.
A proven unchanged failed exchange may resume the old grant/record, but its
pending actions remain set aside and caches refetch. Ambiguous custody requires
Finish to discard the uncertain local sign-in and reconcile disabled access.

Root now reserves its connection transaction before draining Portal's Edge and
native admission. Both gates remain held across token exchange, record changes,
owner apply and a newly proved runtime process. Finish recovers interrupted
exchange without replaying an authorization code, and revalidates a committed
runtime after a restart before completing release. A completed failed attempt
keeps its explanation while permitting a new sign-in; uncertain legacy attempts
remain blocked until recovery.

Controlled crash tests, real disposable admission gates/native activity and
systemd producer-stop controls qualify these components. This does not certify
the complete installed account-change journey or real Calendar/draft writes.
The current real-account qualification grants read access only.

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
