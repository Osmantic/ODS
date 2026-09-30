# Linux/WSL Pixel source upgrades

## Final runtime overlay and access proof

The vendor release receipt describes the release configuration. The ODS installer
may subsequently apply its fixed runtime-budget overlay. Completing that source
transaction requires reproducing the final configuration **byte for byte** from
the original authenticated release snapshot and the renderer in the protected
source plan. It does not rewrite the original release or access receipts.

The coordinator reconstructs the Full Access revocation baseline from the
original candidate and before-receipt, repeats derivation after a fresh runtime
probe, and rechecks source, snapshots, process identity, proof and held admission
before recording a separate completion. Cloud route fingerprints and local
model identity come from the original snapshot. Sandbox follows its existing
fresh-proof completion path; this derivation never enables Full Access.

If the original snapshot lacks the inspector or project socket, the new channel
also needs a protected deployment configuration for the same owner, service
files matching the source plan, the installer's exact unit contract, an active
unit without extra drop-ins, a matching Unix peer PID, and immutable installed
image identity. Inspector image validation uses the original installer's closed
image contract. These checks are repeated around rendering; changed provisioning
keeps admission held. Project support is available only when that source plan
contains its installer and runtime files. Existing tool authorization still
applies; a socket does not grant project execution authority.

This is a proof of the installed channel and deterministic configuration overlay,
not an attestation of how a Docker image was built. The isolated tests use actual
installer validators and Unix peer credentials with explicit disposable Docker
and systemd adapters; they do not qualify a complete systemd/Compose installation.
After downstream mutations, failure remains **resume-only for the same reviewed
candidate**, not a promise of global rollback.

An existing ODS-managed Pixel installation can change public source releases
without uninstalling its access controller or recreating its permission
preference. The installation owner and installation directory must remain the
same. macOS keeps its existing installer path. Disabling Pixel still uses the
real managed uninstaller.

## What is preserved

The upgrade records the original managed marker, configuration and access
receipt hashes. It snapshots the source files before replacing them, installs
the protected coordinator from those staged bytes, and acquires the existing
native and external admission gates. A root-only local operation binds that
hold to the exact staged transaction. Ordinary model-finish calls cannot release
an incomplete source upgrade.

Sandbox stays Sandbox. Full Access is preserved only through its existing
validated receipt and release transaction; neither a configured mode nor an old
`verified.json` is runtime proof. The installer must obtain a fresh proof of the
running process, configuration and service boundary before releasing admission.
This does not approve protected Operations plans or remove their fresh
authentication requirements.

## Interrupted installation

Re-run the **same reviewed installer and candidate source**. The installer
recognizes its retained source transaction and resumes it rather than
uninstalling the previous runtime. Changed source bytes, a different owner,
foreign hold, a changed permission receipt during a held transaction, symlink,
or ambiguous custody stop the upgrade. If the owner changes the preference
before any hold or source copy, re-running the same candidate can record that
current preference under the coordinator lock; it never restores the previous
permission receipt. Do not delete access journals or copy old proof files to
recover.

Before the ordinary directory, environment, Compose and native-service phases
begin, the staged source can be restored under the same hold. This is a
**source restoration**, not a rollback of every installed component: the newer
protected coordinator remains installed so that an older guard cannot release
an incomplete recovery. The restored source and retained coordinator are both
verified before fresh runtime proof and release. Use the reviewed installer or
uninstaller that understands this retained coordinator; an older uninstaller
may correctly refuse it.

After that boundary, recovery is **resume of the same candidate only**. The
installer does not claim to reverse container, environment, package or native
service changes. A later failure leaves admission held and requires finishing
that update. A lost reply after completion can be replayed without applying the
release again.

Once a transaction is fully released, the next update captures a new baseline,
including owner-installed extensions. A historical completed update does not
freeze the installation's source tree indefinitely. The retained protected
coordinator inventory is still checked.

Source writes use one root-owned, mode `0700` staging directory at the top of
the installation, outside the source trees being inventoried. Its exact random
name and directory identity are recorded in root-private state before use.
After a process interruption, only that private directory's known `payload`
file can be removed; unknown contents, links or a replaced parent/directory
stop recovery without deleting them. The empty directory remains for later
upgrades and is checked by the managed uninstall inventory. No name-prefix
cleanup is performed. Destination filesystem checks run before acquiring the
hold; source trees mounted on another filesystem require relocating those
trees before retrying, rather than a non-atomic copy fallback.

A normal reinstall after completion may enable or add owner extensions, but
cannot silently change the retained protected coordinator's bytes, permissions
or ownership. Such a coordinator change requires a newly staged source upgrade.

## Qualification scope

Filesystem and coordinator regressions exercise exact source snapshots,
interrupted copy and restore, acquisition recovery, preserved permission mode,
foreign handles, changed files, links, completion barriers, repeated updates,
and local socket peer authentication. The Phase06 handoff test executes only its
extracted branch with all commands stubbed and no external programs on PATH.
Actual unprivileged child processes are also killed during temporary-file
creation, partial writes, fsync and either side of rename, for both update and
restore. Recovery must finish the exact planned tree after each interruption.

These tests do not simulate a complete privileged installer or prove a physical
Windows reboot. The public runtime version/bundle must also pass its independent
provenance and release qualification before this upgrade is published.
