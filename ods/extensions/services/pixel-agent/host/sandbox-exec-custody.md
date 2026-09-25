# Docker execution custody

OpenClaw 2026.6.33 terminates the host `docker exec` client on timeout or abort.
That does not terminate the command inside the container. The retained ordinary
SDK regression returned a timeout after 1.06 seconds, then its Python child wrote
to the workspace four seconds after launch.

The four exact-hash compatibility recipes add custody to the existing Docker
backend and its existing `finalizeExec` hook. They do not add a host execution
service. ODS enables `ODS_EXEC_CUSTODY=1` only in its managed, Python-capable
sandbox profile. Profiles without that flag keep the original Docker command.
Native host and SSH execution do not use the custody helper.

The original `/bin/sh -lc` command, environment, workdir, stdin and streams remain
on the ordinary SDK execution path. An unprivileged in-container subreaper owns
each execution until the SDK acknowledges its outcome. Every execution gets a
private directory, random nonce and independent ownership token. Finalization
consumes one startup frame from the original Docker stream before the command
is admitted. The backend pins its PID/start ticks in host memory; a separate
fixed kernel probe releases the startup barrier. Later frame-like command bytes
remain ordinary stdout and cannot replace the binding. Finalization checks the
directory inode and the host-held process identity, then signals through a pidfd.
It never trusts guest-written owner/completion records. Custody probes use an
absolute /bin/sh and the root-owned system Python executable pinned before
admission, so a later command-created sh/python3 in a writable PATH directory
cannot replace the probe. The original command keeps its configured PATH. The independent probe
stops the subreaper, drains its live kernel ancestry, and confirms the exact
supervisor's termination before acknowledging cleanup. Timeout, abort and transport failure drain only that execution's process
ancestry, including `setsid`, double-fork and newly spawned descendants. An
unrelated concurrent command remains outside that ancestry.

For the existing canonical ODS cancellation wrapper, custody also observes only
that wrapper's bound, read-only run marker. It lets the wrapper perform its
bounded cancellation, then drains descendants that escaped its process group.
This covers cancellation after SDK backgrounding without guessing from exit130.
Custody never creates or clears the marker.

Normal shell completion releases custody and preserves ordinary detached-child
behavior. **Normal completion is not proof that descendants are quiescent.** A
release receipt explicitly records `descendantsDrained: false`; it must not grant
additional preview recovery, verification or host-admission authority.

Unproved cleanup throws `ODS_SANDBOX_EXEC_UNSETTLED`, preserves the private
ownership records, and retains a nonterminal process session. Its dead host
Docker-client PID is removed so process fallback cannot signal a reused PID.
There is no automatic retry, unrelated-process kill, cancelled-marker reset or
claim of successful cleanup. Owner recovery must settle that exact execution.

This repair establishes child-lifecycle evidence, not raw-output completeness.
The Docker client may already have disconnected its output streams on timeout.
Raw-capture publication must independently establish byte completeness and must
remain incomplete when it cannot do so.

`sandbox-exec-custody.py` is the readable source embedded verbatim in the backend
recipe; its contract test prevents the two copies from diverging. The installer
uses the existing owner-private backup, exact-hash repair and restore mechanism.
This does not turn unrelated programs sharing a sandbox UID into isolated
security tenants. If the pinned supervisor disappears or changes identity,
its children may have reparented: that is uncertain, never a successful drain.
An initial missing/malformed ownership frame cannot admit the command.

The actual SDK integration suite uses isolated nonroot, networkless, read-only
containers and no inference. It exercises detached children, late writes,
timeout/abort races, concurrent jobs, mismatched ownership and retained failures.
