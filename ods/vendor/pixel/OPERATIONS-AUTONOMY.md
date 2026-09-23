# Operations autonomy and action packs

Pixel 3.1 separates capability from authority. An action being installed does not mean
Pixel may run it automatically. The Operations Broker compiles every request against a
private policy, resolves its current authority, reserves its budgets, and records a
decision receipt before execution.

## Authority levels

| Level | Meaning |
|---|---|
| `disabled` | The action cannot be requested. |
| `observe` | Eligible read-only observations may run; no state change can be proposed. |
| `propose` | The broker creates an immutable plan that requires external exact-hash approval. |
| `bounded-auto` | An active grant may execute only when every action, target, tier, environment, parameter, time, concurrency, and resource constraint matches. |

The default policy level may only be `disabled`, `observe`, or `propose`.
`bounded-auto` exists only in a validated standing grant or temporary lease. Break-glass
shell is always `propose`. Production automation and every `change`-tier automation
require a temporary external lease even if a standing grant appears to allow them.

## Decision path

```text
typed request
   -> validate policy/action/target/parameters
   -> compile immutable plan
   -> resolve default plus active grants and leases
   -> enforce production/change hard rules
   -> reserve execution/concurrency/failure budgets
   -> record constraints hash and decision receipt
   -> recheck pause and authority immediately before execution
   -> run fixed argv through the isolated broker
   -> release budget and append immutable audit evidence
```

Machine output, repository text, email, Calendar entries, web pages, and the model
cannot issue or widen a grant. Only the operator-side CLI can install a lease. Lease
IDs are single-use: revocation and expiry cannot be undone by recreating the same ID.

## Policy schema v2

Use `schemas/operations-policy-v2.schema.json` as the machine-readable contract. Each
target has an explicit `environment`: `development`, `test`, `staging`, `production`,
`lab`, or `unclassified`. Each action declares an accurate `effect`,
`defaultAuthority`, `idempotent`, and `reversible` value. An automatically managed or
changed action must also name a verification action and have rollback metadata.

Standing grants are suitable for recurring low-risk work in dedicated lab, test, or
staging runners. Elevated grants must name exact actions and targets, constrain every
parameter, and include execution, concurrency, runtime, and failure budgets. The
broker rejects wildcard elevated grants.

The v1 migration is explicit and never overwrites its input or an existing output:

```bash
./pixel ops-policy-migrate /secure/client/policy-v1.json \
  /secure/client/policy-v2.json \
  --environment tower2=production \
  --environment worker-a=lab \
  --update-onboarding /secure/client/onboarding.json \
  --confirm
```

Migration emits compatibility grants so prior behavior is reviewable. Replace them
with action- and target-scoped grants before normal operation. Classifying a target as
production immediately activates the v2 rule that automatic work needs a lease.
When `--update-onboarding` is used, the helper requires the onboarding file to reference
the exact input policy, creates a private backup, and atomically points it to the output.

After reviewing and replacing any standing automation, remove only the generated v1
compatibility grants with another backed-up atomic operation:

```bash
./pixel ops-policy-tighten /secure/client/policy-v2.json --confirm
```

The command refuses a v1 policy, a malformed authority block, or a policy that has
already been tightened. It never removes ordinary v2 grants.

## Temporary authority leases

Lease files follow `schemas/operations-authority-lease.schema.json`. Keep them private
and exact. For example, this permits two verified fixture restarts on one production
target for at most 30 minutes:

```json
{
  "id": "fixture-restart-20260805",
  "level": "bounded-auto",
  "actions": ["service.restart"],
  "targets": ["worker-a"],
  "tiers": ["managed"],
  "environments": ["production"],
  "parameterConstraints": {"service": {"values": ["pixel-fixture"]}},
  "maxExecutions": 2,
  "windowSeconds": 1800,
  "maxConcurrent": 1,
  "maxRuntimeSeconds": 300,
  "maxOutputBytes": 262144,
  "maxFailures": 1,
  "allowProduction": true
}
```

```bash
./pixel ops-authority grant /secure/client/fixture-restart.json 30 --confirm
./pixel ops-authority show
./pixel ops-authority audit 100
./pixel ops-authority revoke fixture-restart-20260805 --confirm
```

Failure budgets are circuit breakers. Once `maxFailures` is reached during the grant's
window, new matching work is denied. `maxConcurrent` is reserved atomically. Resource
limits further cap runtime, output, and artifacts. Authority is checked again before a
job starts; revocation stops queued or running read/staging work. A managed transaction
already changing state is allowed to complete verification or rollback rather than
being killed in an unsafe intermediate state.

## Emergency stop

```bash
./pixel ops-pause "investigating unexpected runner output" --confirm
./pixel ops-authority show
./pixel ops-resume "incident closed; policy reviewed" --confirm
```

Pause rejects new execution and stops active read/staging jobs. As with revocation, an
in-flight managed transaction finishes its bounded verify/rollback sequence. Pause and
resume events include the external operator reason in the authority audit.

## Reusable action packs

`deploy/ops-broker/action-packs.example.json` provides named host, repository,
artifact, service, immutable deployment, staged-package, and reboot-proposal actions.
The reusable file contains a placeholder target and no client identity. Map it to real
targets in private onboarding:

```json
{
  "operationsPolicyFile": "/secure/client/policy-v2.json",
  "operationsActionPacks": [
    {
      "file": "/opt/pixel-source/deploy/ops-broker/action-packs.example.json",
      "targets": {"example-worker": ["worker-a", "worker-b"]},
      "skipActions": []
    }
  ]
}
```

The operator helper updates a private onboarding file atomically and makes a backup:

```bash
./pixel ops-action-pack /secure/client/onboarding.json \
  /opt/pixel-source/deploy/ops-broker/action-packs.example.json \
  example-worker worker-a,worker-b --confirm
```

Configure rejects unknown or partially mapped targets. Pack actions cannot replace a
base-policy action. During an upgrade, an intentional legacy overlap may be named with
`--skip-action ACTION`; the helper records it in private onboarding. Skipping an action
referenced by a pack authority grant is rejected so a grant cannot silently attach to
a different base action. Grants from the example pack are limited to `lab` and `test`;
they do not authorize production.

## Runner installation

Enroll once, refresh versioned helpers without changing trust, then install private
root-owned configuration:

```bash
./pixel ops-target worker-a operator-worker-a worker-a-host --confirm
./pixel ops-target-refresh worker-a operator-worker-a worker-a-host --confirm
./pixel ops-target-actions operator-worker-a \
  /secure/client/actions.json /secure/client/managed.json --confirm
```

`actions.json` maps public names to repository roots, fixed recipes, service units, and
collection sources. `managed.json` maps root-owned service, immutable-release, package,
and rollback commands. The `pixel-runner` account cannot edit either file. Sudo exposes
only the validating managed helper, never a general shell or package manager.

The packaged operations are:

- read: bounded host/process/GPU/service/repository status, checksums, comparisons;
- staging: fixed repository fetch/checkout/recipes, collection, no-symlink archive,
  exact package verification;
- managed: verified service restart/rollback and atomic immutable release
  activation/rollback;
- change: exact hash-bound package installation and rollback through root-owned fixed
  commands;
- approval only: cleanup without recovery, host reboot, and break-glass shell.

Package installation first copies the exact hashed regular artifact into a root-owned
verified quarantine. This closes the hash-to-install replacement window. Release roots
must be root-owned and non-writable outside test mode. Archives use no-follow traversal
and reject symlinks. Managed failures automatically attempt rollback and report both
the primary and rollback outcome.

## Deployment and acceptance

Use disposable fixtures before real applications. Prove read, staging, successful
managed change, failed verification plus rollback, exact package install plus rollback,
pause, lease revocation, failure circuit, output bounds, hostile output, and approval
only behavior. Never exercise reboot or arbitrary shell on a production host merely to
claim coverage; verify that they remain proposals.

Run the local gates and installed-agent harness:

```bash
./pixel test
./pixel pressure 10
python3 security-evals/operations-live/render-cases.py --help
```

Keep live manifests, transcripts, lease files, private policies, runner mappings, and
evidence outside Git. A passing suite demonstrates the tested boundaries and
capabilities; it is not permission to widen a client's policy.
