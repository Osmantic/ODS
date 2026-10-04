# Security Policy

ODS is local infrastructure that can manage Docker, models, secrets,
network exposure, and host-side installer state. Please report security issues
privately before opening a public issue.

## Report A Vulnerability

Report privately through GitHub:
[Security → Report a vulnerability](https://github.com/Osmantic/ODS/security/advisories/new).
If you cannot use GitHub, email `security@osmantic.com` with the subject
`Security report`. Please do not open a public issue for a vulnerability, and do
not post exploit details, secrets, logs, or proof-of-concept payloads publicly.

We acknowledge reports within 48 hours, keep the discussion private until a fix
and advisory are ready, and credit reporters in the advisory unless they prefer
otherwise. Published advisories are listed under
[Security advisories](https://github.com/Osmantic/ODS/security/advisories).

## Security Documentation

- [Security guide](ods/SECURITY.md) covers operator hardening,
  generated secrets, network binding, and service exposure guidance.
- [Security audit receipts](SECURITY_AUDIT.md) track historical findings,
  remediation status, and regression evidence.
- [Installer trust](ods/docs/INSTALLER_TRUST.md) explains inspect-first
  install paths, release-ref pinning, and current provenance limits.
- [AI workflow guardrails](ods/docs/AI_WORKFLOW_GUARDRAILS.md)
  documents how AI-assisted automation is constrained by human review,
  protected paths, and validation.

## Supported Code

Security fixes land on `main` first. The README one-line installers install
from `main`, so new installations receive fixes as soon as they merge.

`ods update` refreshes the container images pinned by your installed version.
It does not install newer ODS code. To pick up code fixes on an existing
installation, follow
[Updating an existing installation](ods/SECURITY.md#updating-an-existing-installation).
Tagged releases are point-in-time source snapshots; check the
[security advisories](https://github.com/Osmantic/ODS/security/advisories) and
[`ods/CHANGELOG.md`](ods/CHANGELOG.md) before pinning one. For release
confidence, see [Release Validation](ods/docs/RELEASE_VALIDATION.md) and the
[Validation Matrix](ods/docs/VALIDATION-MATRIX.md).

## Public Exposure

ODS defaults to localhost-bound services. Treat LAN exposure, reverse
proxy changes, OAuth credentials, owner-card access, and extension installation
as high-risk surfaces. Do not expose a default install directly to the public
internet without an additional security review and deployment boundary. The
[trust boundary](ods/SECURITY.md#trust-boundary) section explains what ODS
treats as trusted on the local machine and the ODS Docker network.
