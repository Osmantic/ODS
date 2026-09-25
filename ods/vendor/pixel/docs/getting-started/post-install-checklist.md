---
title: Pixel post-install checklist
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation]
sources_of_truth: [DEPLOYMENT.md, ACCEPTANCE-CHECKLIST.md, OPERATIONS.md, UPGRADE.md, INCIDENT-RESPONSE.md, SUPPORT.md, scripts/verify.sh]
last_verified_at: 2026-08-27
---

# Pixel post-install checklist

Run this checklist immediately after the first apply. It is a handoff aid, not a substitute for the full [acceptance checklist](../../ACCEPTANCE-CHECKLIST.md).

## Installation identity

- [ ] Record the exact release/source identity and reviewed plan hash in the client handoff.
- [ ] Confirm the dedicated non-root deployment owner and the intended install directory.
- [ ] Confirm the canonical private onboarding file is owner-only and matches the reviewed input.
- [ ] Confirm generated files are ignored output and private credentials are not in Git.

## Installed-host verification

```bash
./pixel verify
```

- [ ] Verify succeeds after activation, not merely before it.
- [ ] The gateway is a hardened service, listens only on loopback, and rejects missing or invalid authentication.
- [ ] Enabled plugins resolve to the active immutable release.
- [ ] `./pixel limbs` matches the approved capability profile and disabled-limb tools remain denied.
- [ ] The fresh runtime attestation matches the installed bytes and active configuration.

A fresh runtime attestation proves the checks in its contract. It does not prove a useful model answer, a live provider qualification, a recovery rehearsal, or client acceptance.

## Credential and authority separation

- [ ] The gateway cannot read source, Operations, or Frontier broker credentials and private policy state.
- [ ] Source projections contain no raw email body, HTML, attachment, or unrelated private content.
- [ ] Operations has no arbitrary-shell authority; production/change work still requires its declared grant or lease boundary.
- [ ] Frontier authentication mode and billing boundary match the selected private policy.
- [ ] Browser setup contains no API key, OAuth token, SSH key, provider session, backup key, or recovery authority.

## First real use

- [ ] Complete the [first-conversation procedure](../use/first-conversation.md) with the configured local model.
- [ ] Confirm the UI shows the configured-agent state before the send control is enabled.
- [ ] Distinguish a succeeded response from a failed or interrupted turn.
- [ ] If a tool is expected, verify its content-free capability/broker evidence instead of relying only on answer text.
- [ ] Do not count model-off, synthetic, mocked, or author self-graded output as the real-backend check.

## Recovery evidence

- [ ] Create a signed, encrypted private-state backup using the exact procedure in [Operations](../../OPERATIONS.md).
- [ ] Keep the `age` decryption identity offline from Pixel.
- [ ] Preserve the trusted allowed-signers file separately from the archive.
- [ ] Validate the archive with the correct key and trusted signer.
- [ ] Rehearse restoration into an empty isolated root; do not overwrite the live deployment for a rehearsal.
- [ ] Record who owns restoration and rollback decisions.

Backup creation alone is not recovery proof. Validation, isolated rehearsal, and live restoration are separate operations.

## Operations handoff

- [ ] The owner has [operations](../../OPERATIONS.md), [upgrade and rollback](../../UPGRADE.md), [incident response](../../INCIDENT-RESPONSE.md), and [support](../../SUPPORT.md) instructions.
- [ ] Monitoring and disk-capacity expectations cover gateway, enabled brokers, projections, receipts, and private-state retention.
- [ ] Credential rotation and emergency containment paths have named owners.
- [ ] The update path preserves exact candidate/source identity and requires its own rehearsal and activation evidence.
- [ ] No previous client's token, session, memory, identity, policy, or model credential remains.

## Acceptance record

Mark the deployment accepted only after the applicable items in [ACCEPTANCE-CHECKLIST.md](../../ACCEPTANCE-CHECKLIST.md) have evidence. Record partial, blocked, synthetic, and not-applicable items explicitly. A green repository test suite does not replace host, recovery, provider, or client-owned acceptance evidence.
