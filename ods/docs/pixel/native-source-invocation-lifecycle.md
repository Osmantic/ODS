# Native Source invocation lifecycle

This is a private Root implementation foundation. It does not enable Connected
apps on Darwin, launch a Source job, install an automatic trigger or qualify a
physical Mac. The public refusals remain in place.

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
- `completed` and `retired` require the fixed Root observation that no Source
  jobs are loaded and no broker-identity process remains. This conservative
  global precondition also covers surviving broker descendants; it does not
  assert the outcome of any Google request. Unknown effects still require the
  broker's original effect journal and reconciliation.
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

The Linux mechanical fixtures use real files, CAS checks, renames, anonymous
pipes and an owned disposable child. Darwin ownership, ACLs, Seatbelt and launchd
observations are explicitly simulated. Remaining implementation includes the
fixed Root executor and triggers, per-job stop/recovery evidence, reboot and
generation/environment rebinding, terminal one-shot inventory retirement,
native connection/installer update and withdrawal/retirement wiring, followed
by the physical Mac matrix.
