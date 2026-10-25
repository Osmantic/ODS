# Separate legacy bootstrap recovery package

This candidate is a separate, explicitly invoked Linux root package. It is
not installed by the ordinary installer and does not update the installed
coordinator, source mirror, guards, or model journals. It is not yet qualified
for physical recovery. The implementation exists for package and target review.

Build with `python3 ods/scripts/build-bootstrap-recovery-package.py OUTPUT.tar`.
The deterministic archive includes a manifest binding every program. Root
provisioning must use the reviewed archive hash and a dedicated immutable path
whose ancestors and files are root owned and not group/world writable. The
manifest is a root custody record, not a publisher signature. No download,
automatic extraction, generic root command, module override, or force option
is provided. The loader must be invoked with isolated Python:

```
sudo /usr/bin/python3 -B -I /REVIEWED/ROOT/PACKAGE/loader.py preflight
sudo /usr/bin/python3 -B -I /REVIEWED/ROOT/PACKAGE/loader.py recover TRANSACTION --current-model MODEL --context CONTEXT --max-tokens OUTPUT
sudo /usr/bin/python3 -B -I /REVIEWED/ROOT/PACKAGE/loader.py resume TRANSACTION
sudo /usr/bin/python3 -B -I /REVIEWED/ROOT/PACKAGE/loader.py cancel TRANSACTION
```

These are proposed invocation forms for reviewed provisioning, not instructions
to run the unqualified candidate against a live installation. `--reasoning`
explicitly selects reasoning. Resume uses the original root-bound target and
package; cancellation requires an unchanged native configuration and no owner
reconciliation. No arbitrary backup can be selected. `native-before.json` is
an audit snapshot bound by the claim hash; this package does not restore it.

Supported preflight requires the exact deployed 32-file bundle at base
`9b1eb117622a0cecd2b166a1f36be345595978e1`, its original systemd unit, a nonroot
owner, an unrelated completed/applied source plan with its original protected
mirror, and no retained release artifacts. Recovery additionally requires the
exact legacy held/error model transaction, preserved token/start hash/mode,
valid current native configuration and process agreement, and available local
inference matching the explicitly confirmed target. Unsupported bundles,
partial native/process disagreement, unavailable inference, source conflicts,
rollback source plans, malformed state, and retained release states remain
held. This restricted route does not solve every interruption stage of #8149.

Before reconciliation the controller acquires both old UID-0 and owner lifecycle
flocks, the original model lock, and the original source-upgrade lock. It binds
the captured package manifest, original journal, source mirror, owner source
hashes/modes, unit, owner, and target in a private root claim. The original
protected installer verification and model-finish operations remain authoritative.
The root bridge is loaded from verified captured bytes; the installed bundle is
not replaced. Owner source is verified against the original applied source plan.

The one-shot root systemd unit uses `KillMode=control-group`, `Delegate=no`,
`NoNewPrivileges=yes`, `ProtectControlGroups=yes`, and a finite runtime. Explicit
CAP_SETUID/CAP_SETGID ambient capabilities permit the controller to drop the
fixed owner worker's UID under the tested systemd/WSL configuration. The worker
has no effective/ambient capabilities or supplementary groups. Its fixed
program comes from the captured package through a sealed memfd. Output and
time are bounded. A subreaper and pidfds kill/reap lingering owner descendants
before further proof/restoration; systemd tears down the entire cgroup after
controller death. Resume requires a drained prior service/cgroup.

The quarantine writes and fsyncs a root claim, exact drop-in and stop marker,
then reads typed `Restart=no`/condition properties before stopping the original
coordinator. A fixed Unix socket uses descriptor-based custody, durable intent
before mkdir/bind, and kernel peer credentials. It exposes only model status
and verification of the bound transaction. No mutation/release command is
accepted on the socket. Partial bind intent can be recovered only after the
prior service/cgroup is drained and quarantine is re-proven.

Owner reconciliation calls the unchanged installed helper with the borrowed
transaction, followed by its existing owner/PID-bound restart/verify path.
Native and Edge proof use the unchanged installed bridge. Local inference and
`ods/current` route proof use the installed host agent, including its existing
WSL/native transport. These physical adapters have been implemented, but not
executed against live ODS by this qualification packet.

The original first finish proves and releases admission. Admission is therefore
not continuously held afterward. Before restoring the coordinator, the package
requires the original completion, no pending transition, fresh matching native
proof/root verified record, current config/PID/start/boundary/route identity, idle
native/Edge gates, and current inference. Stored completion or idempotent finish
alone is not fresh proof. A drift refusal keeps the coordinator quarantined but
cannot claim the already released admission gates were closed again.

Remaining qualification includes reviewed root provisioning, the complete new
controller against a disposable old bundle, actual owner/native/Edge/inference
recovery on a reviewed interrupted target, forced restart behavior under NNP,
and reboot recovery. The local descendant boundary does not sandbox an already
authorized owner's other host processes or external service-manager/daemon IPC.
Hostile root, arbitrary concurrent owner edits, and uncoordinated alternate
installer lock roots are outside the exclusion proof. Every current package
refusal must preserve root custody; it never deletes a claim or journal to retry.
