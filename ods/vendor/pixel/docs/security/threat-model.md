---
title: Pixel threat model guide
doc_type: concept
audience: [security-reviewer, maintainer, contributor]
feature_status: mixed
owners: [security, architecture]
sources_of_truth: [THREAT-MODEL.md, SECURITY.md, DEEP-WORK.md]
last_verified_at: 2026-08-27
---

# Pixel threat model guide

The security objective is to let a capable local assistant read bounded projections and request narrowly typed work without allowing untrusted content or a compromised model-facing process to acquire credentials or execution authority.

## Principal attack paths

| Attack path | Primary control | What remains true |
|---|---|---|
| Prompt injection in email or a page | Raw-source separation, untrusted labeling, bounded projection, independent policy compilation | A sanitizer can miss a novel semantic attack; projections never authorize follow-on work |
| Model supplies arbitrary command arguments | Gateway sends names and typed parameters; broker resolves fixed commands from private policy | The model may request any operation the owner has actually allowed |
| Gateway compromise | Distinct identities, private broker state, no credential path in gateway environment | Process isolation is a software boundary and must be verified on the host |
| LAN exposure, DNS rebinding, or browser smuggling | Exact loopback binding, strict Host/origin/framing/session checks, closed schemas, no CORS | Reverse proxies and LAN bindings are outside the supported control-surface boundary |
| Approval replay or plan substitution | Per-process review token and approval bound to immutable plan identity | An operator can still approve a dangerous exact plan |
| Operations output becomes authority | Result is bounded, redacted, and labeled untrusted; policy compilation is separate | Output can inform later review but cannot grant policy or approval |
| Frontier prompt injection or secret egress | Typed task classes, privacy compilation, provider/profile allowlists, budgets | A privacy compiler can miss obfuscated sensitive content; a remote provider is not an air gap |
| Courier SSRF or proxy smuggling | Public-address checks, canonical host handling, inspected-address connection, explicit ports | Browser engines still require prompt security updates and must not be used as authenticated browsers |
| Knowledge deletion is resurrected | External key custody, authenticated encryption, authoritative deletion ledger, quiescence | Deep Work runtime is development-disabled; source contracts do not establish an active feature |

## Trust-zone rule

No component should be trusted for both untrusted-input interpretation and final authority over the resulting action. Source brokers hold source credentials but do not authorize from content. The gateway creates typed requests but does not hold source truth or policy. Operations and Frontier brokers compile policy but do not treat command output or remote advice as authority. Actuation stays behind a separately reviewed identity and approval boundary.

## Deliberate residual risks

- Owner policy can intentionally authorize powerful operations.
- Dedicated users, containers, and runners are software isolation, not a hardware air gap.
- Human approval can be mistaken.
- Sanitizers and privacy classifiers can fail on novel or obfuscated input.
- Availability attacks may remain within enforced resource ceilings.

These residual risks must be visible in review and evidence. Do not rewrite them as guarantees.

## Review a change

For every new input, tool, broker, provider, extension, or storage path, identify: untrusted bytes; credential custodian; policy compiler; approval authority; actuator identity; network reachability; persisted evidence; failure behavior; rollback; and negative tests. If one component collapses several of those roles, require a new security design review.

See the [boundary reference](boundary-reference.md), [extension-point guide](../extend/choose-an-extension-point.md), and authoritative `THREAT-MODEL.md` for the complete attack catalog.
