# Security Policy

Dream Server is local infrastructure that can manage Docker, models, secrets,
network exposure, and host-side installer state. Please report security issues
privately before opening a public issue.

## Report A Vulnerability

Use GitHub's private vulnerability reporting for this repository when available.
If you cannot use private reporting, open a minimal public issue that asks for a
maintainer contact path without including exploit details, secrets, logs, or
proof-of-concept payloads.

## Security Documentation

- [Security guide](dream-server/SECURITY.md) covers operator hardening,
  generated secrets, network binding, and service exposure guidance.
- [Security audit receipts](SECURITY_AUDIT.md) track historical findings,
  remediation status, and regression evidence.
- [Installer trust](dream-server/docs/INSTALLER_TRUST.md) explains inspect-first
  install paths, release-ref pinning, and current provenance limits.
- [AI workflow guardrails](dream-server/docs/AI_WORKFLOW_GUARDRAILS.md)
  documents how AI-assisted automation is constrained by human review,
  protected paths, and validation.

## Supported Code

Use tagged releases for stable installs and downstream forks. The `main` branch
moves quickly and is validated continuously, but it is still the development
line. For release confidence, see
[Release Validation](dream-server/docs/RELEASE_VALIDATION.md) and the
[Validation Matrix](dream-server/docs/VALIDATION-MATRIX.md).

## Public Exposure

Dream Server defaults to localhost-bound services. Treat LAN exposure, reverse
proxy changes, OAuth credentials, owner-card access, and extension installation
as high-risk surfaces. Do not expose a default install directly to the public
internet without an additional security review and deployment boundary.
