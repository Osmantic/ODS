---
title: Configure Pixel source limbs
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, configuration, security]
sources_of_truth: [onboarding.example.json, scripts/configure.mjs, scripts/authorize-google.sh, scripts/install-source-broker.sh, DEPLOYMENT.md, CLIENT-ONBOARDING.md]
last_verified_at: 2026-08-27
---

# Configure Pixel source limbs

Email, Calendar, and social inputs use a projection boundary. The Source Broker owns source credentials and publishes sanitized typed data; the gateway receives neither the credential nor raw stored content.

## Select sources

Review the individual email, Calendar, direct-Calendar, and social flags. Enabling Calendar read projection does not automatically enable direct mutation. Bounded direct Calendar shapes and consequential approval remain separate policies.

For Google sources, the default OAuth token input is `~/.config/pixel-google-workspace/token.json` through `PIXEL_GOOGLE_TOKEN_PATH`; deployments may configure another private path. This is private host state, later moved under Source Broker custody. Do not inspect, copy, or attach its contents.

## Authorize and install

After the client owns the OAuth project and scopes:

```bash
./pixel authorize
# Requires exact confirmation; review before running.
./pixel source-broker --confirm
```

Then rebuild the deployment plan, apply, and verify. Confirm the gateway owner cannot read the broker-owned credential and can only read validated projections.

## Failure and change

Authorization success does not prove projection completeness or API quota. Use source-specific acceptance for pagination, metadata-only Sent handling, injection resistance, and proposal/reconciliation behavior.

Disabling a source in configuration removes its intended plugin/service requirement but does not revoke issuer-side OAuth access or decide retention. Stop the source services, revoke at the issuer when required, and process broker state under the client's retention policy.
