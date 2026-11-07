# Native Source invocation lifecycle

This is a private Root implementation foundation. It includes a fixed Source
executor and explicit triggers, but Connected apps on Darwin remain unavailable
until the remaining connection and deployment work and physical qualification
are complete. Existing public refusals remain in place. Installing the code
does not create an execution journal, enable triggers or grant provider access.

`pixel-native-source-broker.py` now publishes nonce-bound transitions through
the same active journal, protected deployment selection and atomic Gate commit
as initial slot preparation. The fixed reader accepts the additional
`quiescing` and `completed` phases so an independent sibling can continue; only
`prepared` and `running` can authorize their own existing dispatch paths.

- The executor may record `running` after its owned READY/GO handoff, while the
  original admission deadline remains valid. This writer supplies no process or
  provider success proof by itself.
- `quiescing` and `unknown` revoke that invocation's admission without waiting
  for its original deadline. They do not signal a process or repeat an action.
- `completed` and `retired` require the fixed global no-actors witness, or the
  executor's fresh per-job proof described below. They do not assert the outcome
  of a Google request. Unknown effects still require the broker's original
  effect journal and reconciliation.
- Explicit rearm is limited to completed/retired `sync` and `direct` slots.
  A new nonce receives the current bounded Root admission ceiling. Sync retains
  its separate original worker budget; direct's worker ceiling cannot exceed
  Root's admission. Action, reconcile, exchange and revoke are never rearmed.
- A retry observes the original committed rearm nonce and deadlines. A changed
  expected nonce, deployment, environment, private namespace or original boot
  refuses. Lifecycle rollback cannot restore previously revoked dispatch.
- Explicit abort of an exact new rearm nonce requires quiescence and can restore
  only its already completed/retired prior Gate. An expired admission is never
  renewed by recovery. After the explicit abort, a new rearm can create a fresh
  invocation; an uncertain effect-bearing one-shot has no such replay path.

Phase/rearm commits preserve the job definition's bytes and inode, so an
unchanged running sibling's code/environment binding and invocation remain
valid. The private manifest precedes the Gate's atomic replacement. Interrupted
image staging and renames resume from their recorded inodes; unknown images are
preserved. A directory creation interrupted before its inode can be journaled
leaves unadoptable residue, as in the existing native preparers.

Committed prior transaction images and metadata receipts move into an
inode-bound, Root-only history directory before the next lifecycle intent.
This bounds the live retained-image map across repeat runs without deleting
interruption evidence. Archived bytes never serve as a live invocation, stop
witness, grant or provider result. Disk exhaustion fails closed; retention or
archival pruning is not implemented by this foundation.

## Fixed Root executor

`pixel_connections/native_executor.py` supplies a request-local adapter for the
existing closed Source Run grammar. It accepts only a sealed deployment
selection, reloads the independently qualified Root publisher, and uses fixed
`launchctl bootstrap system`, `kickstart system/<label>` and `bootout
system/<label>` commands. It never uses `kickstart -k` or signals a numeric PID.
The existing broker wrapper owns its READY/GO pipes and actual Source child;
Root records its observed wrapper without claiming ownership of that process.
The public slot stays prepared while that wrapper establishes its own existing
handoff. A private observed phase does not change the public worker contract.

The protected CAS journal `source-execution.json` records admission intent and
the original bounded raw-clock ceiling before publication. Initial executable
admission and repeat-run rearm cannot exceed that ceiling. The publisher stores
its ceiling provenance, so a prior data-only prepared slot cannot silently
become an executable admission. Recovery preserves the original nonce and
deadlines even when a publication reply was lost.

Each invocation advances through bootstrap-intent, loaded, start-intent, queued,
observed, quiescing and stopped, or unknown. Bootstrap is inert because the
fixed definition has RunAtLoad and KeepAlive false. Root persists start intent
before its single kickstart. A lost reply is observed through the exact loaded
definition, run counter and broker UID/GID/executable/PID/birth tuple; an
uncertain start is never replayed. Interrupted bootout and final Gate retirement
resume from durable intent. A one-shot retry returns its original receipt,
including uncertain outcomes, rather than minting another invocation.

Before bootout, Root revokes the public slot and persists its known process
tree. Retirement requires the exact label absent and every known descendant
birth absent or replaced. A fresh broker-UID process inventory may exempt only
independently bound live sibling trees, rechecked around that inventory. Inert
or concurrently completed siblings grant no exemption. The fresh stopped job
and canonical hash are bound into the lifecycle transaction, and the publisher
re-runs that fixed verifier before every mutation. Cached PIDs and history
receipts never grant stop authority. A changed tree or unbound broker process
refuses retirement/rearm; Root sends no signal to a possibly reused PID.

The existing Root access daemon runs an optional internal worker only when the
protected module is present. Absent execution state causes no discovery,
provisioning or defaults. Explicit enable/disable records control one-minute
sync ticks and direct proposal metadata changes. The direct trigger reads only
bounded names/inodes/sizes/timestamps, leaving business parsing to the existing
Source broker. Due intent is persisted before admission. Active/unknown jobs
cannot be automatically replaced; uncertain stopped jobs require explicit
recovery. The worker uses the same bounded Root lock as requests and has no
global mutable adapter.

The Linux mechanical fixtures use real files, CAS checks, renames, anonymous
pipes and owned disposable children under the qualified Python executable.
Darwin ownership, ACLs, Seatbelt and launchd observations are explicitly
simulated. These tests cover lost admission/rearm/start/stop/retirement replies,
simultaneous completion, sibling drift, PID reuse and repeatable fresh nonces.
They are not physical Mac evidence.

Remaining implementation includes the native connection backend and broker
owner/environment writers, reboot and generation/environment rebinding,
explicit abandoned-admission recovery, terminal one-shot inventory retirement,
and installer update/withdrawal/retirement wiring, followed by the physical Mac
matrix. A pending admission that expires before an executable job is journaled
fails closed without renewing its ceiling. These dependencies still block
public Darwin availability.
