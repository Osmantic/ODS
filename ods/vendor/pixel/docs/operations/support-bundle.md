---
title: Prepare a safe Pixel support packet
doc_type: how-to
audience: [owner, operator]
feature_status: supported
owners: [documentation, operations, security]
sources_of_truth: [SUPPORT.md, OPERATIONS.md, INCIDENT-RESPONSE.md, CONTROL-SURFACE.md]
last_verified_at: 2026-08-27
---

# Prepare a safe Pixel support packet

Pixel does not provide a command that packages private state for support. The encrypted backup is a recovery artifact, not a support attachment. Build a minimal, manually reviewed, sanitized packet instead.

## Include

- Pixel release and exact source identity, without private filesystem paths;
- host distribution and release, rounded capacity only when relevant;
- selected deployment/capability profiles and the affected enabled limb;
- the exact command or owner action that failed;
- expected result, actual result, timestamp, exit code, and sanitized error text;
- whether `./pixel verify` passed, failed, or was not applicable;
- whether the issue is reproducible on a non-sensitive fixture;
- content-free receipt/state identifiers or hashes only when the owning contract permits sharing;
- the smallest journal excerpt needed to show the failure, after manual review.

## Exclude

Never include onboarding files, `.env`, environment dumps, OpenClaw configuration, credentials, tokens, private messages, memory, transcripts, prompts, email/calendar content, hostnames, host keys, local paths, private policies, approvals, broker plans/results, session files, browser review URLs, provider capsules, backup archives, decryption identities, or raw control-state/log directories.

Do not use automated redaction as the only review. A secret scanner cannot identify every client-private value or linkable identifier.

## Reproduction format

Use public placeholders and state the evidence boundary:

```text
Release/source identity: <public exact identity>
Host lane: <documented distribution and release>
Profile / affected limb: <profile> / <limb>
Command or action: <sanitized exact shape>
Expected: <bounded outcome>
Actual: <sanitized outcome and exit code>
Verification: <pass | fail | not-applicable>
Live evidence: <none | model-only | tool | host | recovery, with limits>
Reproduction: <public-fixture steps>
```

## Route the report

Use a normal GitHub issue for ordinary defects or feature requests. Report suspected vulnerabilities privately through GitHub's security-advisory flow or another existing trusted administrator channel. If execution, privacy, or credentials may be compromised, contain first using [incident response](../../INCIDENT-RESPONSE.md); Pixel is not an emergency service.
