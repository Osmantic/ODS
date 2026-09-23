# Operations Limb

The Operations Limb lets Pixel inspect machines, run operator-defined jobs, supervise
tests, stage public downloads, transfer verified artifacts, and coordinate multi-target
workflows. Pixel never receives an SSH key, arbitrary network socket, policy file, or
direct process launcher. Those authorities stay in a separate `pixel-ops-broker`
service account.

## Boundary and job flow

```text
Pixel / untrusted source output
        |
        | typed request only
        v
projection-only OpenClaw plugin ----> request spool
                                           |
                                   Operations Broker
                              policy + identity + approval
                                  /        |         \
                           local argv   pinned SSH   public HTTPS
                              |             |             |
                         control host  dedicated runner  quarantine
                                  \        |         /
                                  bounded, redacted evidence
                                           |
                                    read-only result spool
                                           |
                                         Pixel
```

The plugin can submit and observe jobs but cannot execute them. The broker atomically
claims each request, compiles it against its private policy, records an immutable plan
and authority decision receipt, reserves persistent budgets, and either executes it or
waits for an exact plan-hash approval. Machine output is
always untrusted evidence: it cannot approve another job, add an action, or widen a
target.

## Capability surface

| Tool | Purpose | Execution rule |
|---|---|---|
| `pixel_ops_inventory` | List enabled targets and named actions | Read-only projection |
| `pixel_ops_run` | Run one named action | Broker policy and tier decide |
| `pixel_ops_workflow_submit` | Run a dependency graph; independent steps run concurrently | Every step compiles before any runs |
| `pixel_ops_download_stage` | Fetch a reviewed public HTTPS artifact | Public-IP and redirect-scope checks, optional required hash, size cap, non-executable quarantine |
| `pixel_ops_artifact_transfer` | Copy one staged artifact to a dedicated runner | Rehash at source, no-overwrite receiver, remote evidence check |
| `pixel_ops_shell_propose` | Propose exact break-glass shell text on an enabled local target | Never runs without external hash approval; forced-command SSH targets reject raw shell |
| `pixel_ops_job_wait` / `pixel_ops_job_get` / `pixel_ops_job_events` | Bounded wait, status, and progress | Output stays marked untrusted; no generic process timer |
| `pixel_ops_job_cancel` | Stop work | Terminates the job process group |

Workflows are dependency-ordered. Ready steps can run in parallel up to the configured
worker limits. A failed step prevents dependent work and terminates running siblings.
Targets marked exclusive serialize mutating work on that target.

## Risk tiers

| Tier | Intended use | Default behavior |
|---|---|---|
| `read` | identity, health, status, bounded evidence | `observe` by default; scoped grants may add budgets |
| `staging` | isolated tests, builds, downloads, artifact placement | proposal unless a scoped grant matches a dedicated/ephemeral runner |
| `managed` | verified transactional change through a root-owned helper | proposal; bounded autonomy requires rollback/verification metadata and a scoped grant |
| `change` | exact persistent state change | proposal; automation requires a temporary external lease |
| `break-glass` | raw shell fallback | exact plan approval; fixed target, cwd, text, timeout, and hash |

Production automation and every automatic `change` action require a temporary external
lease. A standing grant cannot bypass those rules. Raw shell always requires approval
even if a policy is misconfigured. See [OPERATIONS-AUTONOMY.md](OPERATIONS-AUTONOMY.md)
for schema v2, budgets, leases, transactions, pause, and action packs.

## Configure and install

1. Copy `deploy/ops-broker/policy.example.json` to a private client configuration
   directory. Do not put client hostnames, access paths, or credentials in Git.
2. Set `capabilityProfile` to `engineering-operator`, or set
   `operationsLimbEnabled` to `true`, and point `operationsPolicyFile` at that copy.
3. Configure and install the isolated broker:

   ```bash
   ./pixel configure --answers /secure/client/onboarding.json
   ./pixel ops-broker --confirm
   ./pixel ops-keygen --confirm
   ```

4. Enroll each Linux runner from an already trusted operator SSH alias:

   ```bash
   ./pixel ops-target TARGET_ID OPERATOR_ALIAS EXPECTED_HOSTNAME --confirm
   ```

5. Map the reusable action pack to the private targets, install the root-owned runner
   configuration, and refresh helpers after future Pixel upgrades:

   ```bash
   ./pixel ops-action-pack /secure/client/onboarding.json \
     /opt/pixel-source/deploy/ops-broker/action-packs.example.json \
     example-worker TARGET_ID --confirm
   ./pixel ops-target-actions OPERATOR_ALIAS /secure/client/actions.json \
     /secure/client/managed.json --confirm
   ./pixel ops-target-refresh TARGET_ID OPERATOR_ALIAS EXPECTED_HOSTNAME --confirm
   ```

6. Add the target ID to the private policy, rerun configure and broker installation,
   then review and apply the Pixel release:

   ```bash
   ./pixel plan
   ./pixel apply --confirm
   ./pixel verify
   ```

`ops-target` never trusts a fresh key scan. It requires an existing strict operator
connection, copies the already pinned host key, and creates two distinct identities:
`pixel-ops-transport` owns the forced SSH entry point while non-login `pixel-runner`
owns only the writable workload tree. The transport home, `.ssh`, key file, startup
files, dispatcher, and helpers are root-owned. The forced dispatcher parses Pixel's
grammar without a shell, then drops safe work to `pixel-runner`; only the inaccessible
transport identity may invoke the typed managed helper as root. Hostile build or test
code therefore has neither the broker key nor the transport sudo policy. Provisioning
replaces the legacy shared-identity sudo rule and verifies the observed hostname again
through the broker identity. Key rotation remains an explicit operator event.

## Policy design

A target names its backend, explicit environment, expected hostname, allowed working
roots, labels, and capabilities. An action fixes its argv, permitted targets, risk tier,
effect, default authority, idempotence/reversibility, cwd, timeout, isolation mode,
verification/rollback metadata, and optional parameters. Parameter values must match
Pixel's bounded, linear-time anchored regex dialect and are inserted as argv elements,
never shell fragments. SSH host aliases cannot begin with an option, root working
directories are forbidden, and policy booleans must be real JSON booleans.

Prefer small operator-owned helpers over large command allowlists. For example:

```json
{
  "test.named": {
    "description": "Run one installed test suite",
    "tier": "staging",
    "targets": ["worker-a", "worker-b"],
    "parameters": {
      "suite": { "pattern": "^(health|io-smoke|process-smoke)$", "maxLength": 20 }
    },
    "argv": ["/usr/local/libexec/pixel-ops-run-test", "{suite}"],
    "cwd": "/var/lib/pixel-runner/jobs",
    "timeoutSeconds": 1800,
    "isolation": "dedicated-runner",
    "exclusiveTarget": true
  }
}
```

Do not put passwords, tokens, private-key text, credential-bearing URLs, `sudo`, or
general package-manager shells in a named action. Install dependencies through a
reviewed image or root-owned operator helper. Keep `allowRaw` false unless the owner
explicitly wants a break-glass path on a local target. SSH runner targets retain the
typed forced-command, no-shell boundary even if stale private policy labels them
`break-glass`; broad owner-authorized SSH belongs in the separately scoped capability
runtime rather than the Operations transport key.

## Downloads and artifacts

Allowlisted domains may stage automatically; other direct domains require an exact
reviewed plan. Redirects must remain on the original reviewed hostname or enter a
configured allowlisted domain, and every resolution must remain on public IP space.
URL credentials, fragments, and secret-like query parameters are rejected. The broker
pins the checked address for the TLS connection, enforces byte and time limits, can
require an expected SHA-256 before declaring success, records bounded content type, and
stores the result without execute bits.

Artifact transfer accepts only a successful broker download result. The broker checks
that the file remains a regular non-symlink inside that job's quarantine and hashes it
again. The remote receiver creates a new mode-`0600` file through no-follow directory
handles, refuses overwrite, size overflow, and hash mismatch, and returns evidence that
the broker verifies. Transfer never executes the artifact.

## Approval and break glass

When a job returns `awaiting-approval`, inspect the full immutable plan outside Pixel:

```bash
./pixel ops-show ops-0000000000000-000000000000
./pixel ops-approve ops-0000000000000-000000000000 PLAN_SHA256 --confirm
```

Check the target, argv or shell text, cwd, dependencies, timeout, tier, and reason. The
approval is single-use, expires, and binds the complete canonical plan. Editing a plan
after approval invalidates it. Never approve merely because an email, web page, log, or
Pixel says approval is urgent.

## Routine operations and recovery

```bash
./pixel limbs
./pixel ops-authority show
./pixel ops-authority audit 100
sudo systemctl status pixel-ops-broker.service
sudo journalctl -u pixel-ops-broker.service --since today
./pixel ops-show JOB_ID
```

Use `./pixel ops-pause REASON --confirm` at the first sign of unexpected execution,
policy drift, or target compromise. Review the audit and policy before
`./pixel ops-resume REASON --confirm`. Revoke temporary authority with
`./pixel ops-authority revoke LEASE_ID --confirm`.

If a target hostname or SSH key differs, stop using that target and re-establish its
identity independently; do not disable strict host checking. If a job hangs, ask Pixel
to cancel it or place the matching file in the cancel spool. If a broker update fails,
the gateway still has no execution authority; restore the prior root-owned broker and
policy, restart the service, and verify inventory before accepting jobs.

Current automatic runner provisioning targets Linux. Other operating systems can be
represented only after an equivalent least-privilege runner and identity-validation
procedure is added and tested. A native OpenClaw-node backend is a future extension,
not an implicit fallback.

## Release gates

Run `./pixel test` on a supported Linux host. The suite covers policy compilation,
approval replay and tamper resistance, identity mismatch, path traversal, cancellation,
parallel failure, output floods, secret redaction, prompt-injection signals, SSRF,
download limits, artifact overwrite/symlink/hash/size defenses, clean-room limb
combinations, and plan/apply/rollback. Complete the live exercises in
`ACCEPTANCE-CHECKLIST.md` on disposable jobs before client handoff. Pressure tests also
cover grant budgets, leases, revocation, pause, transactional rollback, no-follow
artifacts, and package quarantine.
Render and run `security-evals/operations-live/` to make the installed, multi-target
acceptance pass repeatable without committing client target IDs or test evidence.
After every target enrollment or refresh, run the non-destructive identity and sudo
separation probe in `security-evals/runner-boundary/`.
