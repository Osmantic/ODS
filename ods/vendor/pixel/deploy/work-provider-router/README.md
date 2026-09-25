# Pixel local-first work-provider router

This additive plane routes work to a local or, only when an owner explicitly opts in, a
remote provider. It is fully local-first: the default and every fallback path can run without
a cloud service. The router itself never performs a provider call, reads a credential, opens a
network connection, or grants execution authority.

## Modes

- `local-only` — selects a currently semantically qualified local provider or rejects. No
  remote policy, credential, qualification, or service is required or considered.
- `explicit-provider` — selects exactly the owner-requested provider (from the request) or
  rejects. Only that provider's policy and qualification are evaluated; there is no fallback
  to or dependency on any other provider.
- `policy-router` — deterministically evaluates the owner-private ordered `preference`
  (always local-first), prefers a qualified local provider, and selects a remote provider only
  when local cannot meet the declared task envelope.

## Safety properties

- **Content-free.** The request and decision schemas carry only task class, data
  classification, sensitive categories, context/tool/vision need, token estimates, and a cost
  ceiling. No prompt, response, reasoning, tool argument, credential, or path is represented.
- **Remote disabled by default.** A remote provider is selectable only if its exact private
  policy SHA-256 is pinned in the owner router policy and a matching private policy is bound.
  There is no silent cloud fallback.
- **No authority.** A routing decision grants no execution, credential, network, merge,
  deploy, publish, external-message, or security-testing authority. Its `authority` block is
  all `false` (`reroutingOnUncertain` is `false`: an uncertain provider outcome stays governed
  by the existing run ledger and cannot trigger rerouting or retry).
- **Sensitive data forces local.** Every sensitive-category value in the request schema,
  including proprietary code, personally identifiable data, raw source bodies, and security
  findings, forces local-only or reject in this release. `confidential` and `restricted` data
  do the same. Public and internal data must separately match the selected private policy.
- **Qualification is hash-bound and expiry-gated.** Remote selection requires a current,
  non-expired, `semantic-capability` qualification whose capability envelope exactly covers the
  request. `connectivity-smoke` attests transport reachability only and is never semantic task
  qualification. Local selection has the same semantic, envelope, hash, and expiry requirement.
  Unknown, future-dated, malformed, or expired qualification fails closed.
- **Cost is conservative and current.** A qualification binds nonnegative input, output, and
  fixed pricing. The router computes a rounded-up estimate with overflow-safe integer arithmetic
  and requires it to fit both the request ceiling and the owner-private per-run ceiling.
- **Deterministic.** Selection follows the fixed owner `preference` order; there is no
  consensus and no random scoring.

## Execution binding contract

A decision returns `{ decision, decisionSha256 }`. The router performs no call. A later
execution layer must bind this exact `decisionId`/`decisionSha256` (for example, recorded in
the run ledger's immutable identity fields) before any adapter is entered. A decision alone
grants nothing.

## Example policy

`router-policy.example.json` demonstrates a disabled-by-default `policy-router` with `preference = ["local",
"moonshot-kimi"]` and a single enabled remote private-policy SHA (replace the placeholder with
the canonical SHA-256 of the owner's Moonshot private policy). Per the product rule, K3 may
only be qualified for `connectivity-smoke` until a separate evaluator attests coding/general
work and current pricing; `semanticTaskClasses` must be covered by a `semantic-capability`
qualification. Operators must also supply a separate current local semantic qualification.
