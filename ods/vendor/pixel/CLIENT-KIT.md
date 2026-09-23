# Pixel client kit

Pixel's reusable delivery boundary has three layers:

1. The signed golden core contains product code, schemas, fixed security boundaries, and
   release evidence. Client data, credentials, negotiated terms, and private identifiers
   never enter it.
2. A private client overlay selects a capability profile, records extension-pack IDs,
   fixes local-first and authentication policy, and binds licensing and usability evidence.
3. Deployment-owned private state supplies credentials, paths, models, targets, and client
   content through the existing onboarding and broker boundaries.

Create an owner-only directory outside the source repository, then generate a draft:

```bash
install -d -m 0700 /secure/pixel-client
./pixel client-kit generate \
  --output /secure/pixel-client/overlay.json \
  --client-id example-client \
  --profile minimal
./pixel client-kit validate --overlay /secure/pixel-client/overlay.json
```

The generator never creates a client agreement or usability claim. A deployment is
`ready` only after the private overlay records reviewed SHA-256 evidence for both.
Pixel's public ODS-only use and distribution grant is in [LICENSE.md](LICENSE.md);
separate commercial or support agreements may add terms but are not required to use
Pixel as part of ODS.

Pixel continues to bind its page to exact IPv4 loopback. The v1 overlay does not enable
remote access. If a client later commissions a separately qualified remote-access adapter,
the external identity provider must enforce authenticator-app TOTP, may offer email
one-time codes as an explicitly configured fallback, and must not disclose authentication
or email-delivery credentials to Pixel. Do not reinterpret this policy as permission to
bind the current control service to a LAN or public interface.
