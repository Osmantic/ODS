---
title: Use Pixel Frontier review
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, product, security]
sources_of_truth: [FRONTIER-LIMB.md, FRONTIER-LIVE-QUALIFICATION.md, CONTROL-SURFACE.md, plugin-frontier/, deploy/frontier-broker/, scripts/frontier_budget.py]
last_verified_at: 2026-08-27
---

# Use Pixel Frontier review

Frontier is an optional typed second-opinion plane. Pixel makes a local attempt first, then the separate broker may keep work local, request local retry/operator context, preview a sanitized capsule, hold it for approval, use a bounded grant/lease, or reject it.

## What can cross

Only typed plan-review and failure-triage capsules are supported. The privacy compiler classifies disclosure, rejects restricted/never-egress content, replaces supported identifiers, and binds the capsule, local-attempt receipt, policy, provider/model, budget, output contract, and authority.

There is no generic prompt, file, repository, URL, message, log, raw source, or credential forwarder. Frontier is not a physical air gap; any allowed capsule crosses the configured provider boundary under the owner's current account and data-handling terms.

## Review and approval

The owner page may show a separately enabled exact awaiting-approval sanitized capsule, but cannot approve it. Use the trusted terminal:

```bash
./pixel frontier-show JOB_ID
# Requires exact confirmation; review before running.
./pixel frontier-approve JOB_ID PLAN_SHA256 --confirm
```

Confidential work requires exact approval. Restricted/never-egress work is rejected. Approval remains subject to policy, authorization, expiry, budget, cache/dedup, quality circuit, and claim checks.

## Usage and budgets

```bash
./pixel frontier-usage
```

Content-free usage separates provider calls, cache/local routing, tokens, failures, estimated cost when operator-supplied metered rates exist, and quality/circuit state. ChatGPT-plan access and API billing are distinct; Pixel's limits do not replace provider-side controls.

The browser may draft a short-lived custom-budget proposal but cannot apply/activate it. The displayed exact terminal handoff rechecks its ID/hash and edits only the private policy budget block; configure/plan/apply remain separate.

## Live qualification and recovery

`./pixel frontier-live-qualify` is a separate explicit path for one fixed low-sensitivity synthetic provider check. Preparation makes no provider call; only the final exact confirmed transmit step may. A successful fixed check proves only that exact provider path.

Pause Frontier on credential, provider-call, cache, routing, budget, or quality anomalies. Reconcile provider-side calls with local ledgers before resume. See [Frontier Limb](../../FRONTIER-LIMB.md) and [incident response](../../INCIDENT-RESPONSE.md).
