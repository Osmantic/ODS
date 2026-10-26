---
title: Configure Pixel source limbs
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, configuration, security]
sources_of_truth: [onboarding.example.json, scripts/configure.mjs, scripts/authorize-google.sh, scripts/install-source-broker.sh, deploy/source-broker/broker.py, DEPLOYMENT.md, CLIENT-ONBOARDING.md]
last_verified_at: 2026-10-25
---

# Configure Pixel source limbs

Email, Calendar, and social inputs use a projection boundary. The Source Broker owns source credentials and publishes sanitized typed data; the gateway receives neither the credential nor raw stored content.

## Select sources

Review the individual email, Calendar, direct-Calendar, and social flags. Enabling Calendar read projection does not automatically enable direct mutation. Bounded direct Calendar shapes and consequential approval remain separate policies.

Three optional answers-file keys narrow a deployment without changing interactive configure:

- `calendarWriteEnabled` (default `true`): `false` keeps Calendar read-only. The three propose tools are neither registered nor allowed, direct execution is off, and the actuator refuses any proposal.
- `gmailWindowDays`: adds `newer_than:<N>d` to the Inbox and Sent queries. Without it the broker indexes every message in those folders.
- `userTimezone`: an IANA name rendered as OpenClaw's `agents.defaults.userTimezone`. Without it the key is not rendered.

For Google sources, the default OAuth token input is `~/.config/pixel-google-workspace/token.json` through `PIXEL_GOOGLE_TOKEN_PATH`; deployments may configure another private path. This is private host state, later moved under Source Broker custody. Do not inspect, copy, or attach its contents.

## Authorize and install

After the client owns the OAuth project and scopes:

```bash
./pixel authorize
# Requires exact confirmation; review before running.
./pixel source-broker --confirm
```

Then rebuild the deployment plan, apply, and verify. Confirm the gateway owner cannot read the broker-owned credential and can only read validated projections.

A deployment that signs in without staging a token in the owner's home can run `./pixel source-broker --prepare` instead. It creates the broker identity, its private directories and units, and starts nothing. Root then writes a sign-in handoff (`<flow>.flow.json` at the start of consent, `<flow>.code.json` when Google returns the code) into `PIXEL_SOURCE_OAUTH_DIR`, owned by the broker identity with mode 0600, and starts `pixel-source-exchange@<flow>.service`. The broker checks the state, calls only Google's fixed token endpoint with the PKCE verifier, stores the token with the scopes Google granted, deletes the handoff, and writes a content-free `<flow>.result.json`. Request `openid` and `email` as well: the token and the result then name the account from the ID token Google returns, which must have the client as its audience and a verified email. The flow's `account` hint is never recorded. `pixel-source-revoke.service` revokes the grant at Google and deletes the token, even when Google cannot be reached; `revoke.result.json` records which happened.

## Status and coverage

Each refresh reads every enabled source on its own. A source that fails keeps its last good projection, which Portal sees as stale, and marks only itself in the content-free `status.json` beside the projections: state, error class (`not-connected`, `invalid-grant`, `missing-scope`, `api-disabled`, `rate-limited`, `network`, `client-invalid`, `blocked-by-admin`, `unknown`), HTTP status, last success and when the failure began. A source that never synced gets an empty projection whose `generatedAt` is `null`. When Google did not grant a source's read scope, the broker records `missing-scope` without calling that API. `./pixel verify` fails on a missing or limb-mismatched status file and warns about each failing source.

Calendar follows `nextPageToken` up to `PIXEL_SOURCE_CALENDAR_MAX_PAGES` pages of 250 events. Its coverage records the synced window, pages fetched, and whether the listing was truncated, and the list tool says when a request falls outside that window.

## Failure and change

Authorization success does not prove projection completeness or API quota. Use source-specific acceptance for pagination, metadata-only Sent handling, injection resistance, and proposal/reconciliation behavior.

Disabling a source in configuration removes its intended plugin/service requirement but does not revoke issuer-side OAuth access or decide retention. Stop the source services, revoke at the issuer when required, and process broker state under the client's retention policy.
