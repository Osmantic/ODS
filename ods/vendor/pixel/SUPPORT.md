# Pixel support

Pixel currently targets technically managed, single-owner deployments on Ubuntu 24.04
LTS and Debian 12. Check `README.md`, `DEPLOYMENT.md`, `OPERATIONS.md`, and
`INCIDENT-RESPONSE.md` before reporting an operational problem.

For ordinary defects or feature requests, open a GitHub issue with sanitized reproduction
steps, the Pixel version, the supported host release, and the affected capability. Do not
include onboarding files, environment dumps, credentials, private messages, memory,
hostnames, host keys, private policy, approval records, or live transcripts.

Report suspected vulnerabilities privately through GitHub's security-advisory flow. If
that flow is unavailable, contact a repository administrator through an existing trusted
channel. Do not create a public issue containing exploit details or private evidence.

Pixel is not an emergency service. If execution, credential, or privacy boundaries may be
compromised, follow `INCIDENT-RESPONSE.md`: pause the affected broker, preserve sanitized
evidence, revoke exposed authority at its issuer, and keep the agent offline until review.
