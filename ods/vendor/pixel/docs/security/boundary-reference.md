---
title: Security boundary reference
doc_type: reference
audience: [operator, security-reviewer, maintainer]
feature_status: mixed
owners: [security, operations]
sources_of_truth: [SECURITY.md, THREAT-MODEL.md, ARCHITECTURE.md, OPERATIONS-LIMB.md, FRONTIER-LIMB.md]
last_verified_at: 2026-08-27
---

# Security boundary reference

Use this page to identify the intended authority split. Verify actual users, groups, modes, services, listeners, environment, and policy on the installed host; repository source alone does not prove the boundary is active.

| Boundary | May hold or do | Must not hold or do | Evidence to inspect |
|---|---|---|---|
| Gateway/model process | User dialogue; typed request creation; bounded result reading | Source credentials, raw source, private broker policy, arbitrary process/network/file authority | Effective service properties, environment, listener, private-path denials |
| Source Broker | Source API credentials; deterministic sanitized projection | Authorize an action from content; expose raw content or token paths to gateway | Projection contract, ownership/modes, negative injection tests |
| Control surface | Loopback owner session; closed-schema fixed actions | LAN/reverse-proxy exposure, generic command/file/log endpoints, credential or recovery entry | Exact bind, Host/origin/framing/session behavior, unauthenticated denials |
| Web Courier | Bounded public-web retrieval through checked egress | Authenticated browsing, private/transition address access, arbitrary protocol/port access | DNS/address decisions, proxy route, container/network properties |
| Operations plugin | Write bounded request; read bounded untrusted result | Process, SSH, target policy, approval, or credentials | Plugin source, request/result schemas, denied overreach |
| Operations Broker | Private target/policy compilation, immutable plan, approval/lease checks | Treat remote advice or output as authority; broaden a pack declaration | Plan/approval/lease binding, forced command, target identity, ceilings |
| Frontier plugin | Submit typed review task; read bounded result | Provider credentials, generic prompts, arbitrary egress | Task schema, privacy decision, provider/profile selection, usage receipt |
| Frontier Broker | Credential custody, privacy compilation, budget and provider policy | Put credentials in argv/environment/plans/UI/receipts; accept substitution | Credential path custody, proxy/worker network split, exact evidence binding |
| Extensions | Only the authority of the selected seam | Add implicit tools, credentials, targets, providers, or approval | Signed limb or exact plugin digest, declarations, enable/bind state, negative tests |
| Deep Work contracts | Inert admission and preparation definitions | Claim running long-horizon behavior or tool authority | Current generated status and release contract; runtime remains development-disabled |

## Filesystem and identity principles

Private credentials, policy, plans, approvals, tokens, and recovery material stay in owner-private or broker-private custody. Shared broker interfaces use only the reader identity required by their contract; narrowing them to the wrong owner can break the design, while broadening group membership exposes authority. Do not copy example modes blindly—compare the installed objects with `SECURITY.md` and the relevant deployment generator.

Operations transport and workload identities remain distinct. Transport is forced-command only and is not an interactive shell. The workload identity is non-login and does not acquire transport credentials or general sudo authority. Host-key identity changes fail closed and require quarantine and owner review.

## Network principles

The owner control surface stays on exact loopback. Sandboxes default to no network unless a narrow broker design explicitly provides egress. Public-web and remote-provider paths validate the selected destination and use a constrained transport rather than granting the model-facing process generic network access.

Use [verify and diagnose](../operations/verify-and-diagnose.md) for installed-host checks and [assurance](assurance.md) for the evidence required before a release claim.
