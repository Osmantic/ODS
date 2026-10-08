# Retiring native Pixel after a stopped gateway

This is an explicit, maintainer-assisted removal procedure for
`native-retirement-stopped-job-needs-witness`. It is not initial-install
recovery, an upgrade, or a reason to edit receipts. It requires administrator
authorization and a planned **Mac restart**. Do not start it during other Pixel
maintenance or if stopping Pixel would interrupt important work.

## Why normal retirement refuses

An exited launchd parent does not prove that its descendants have exited. Normal
retirement snapshots live process identities before stopping jobs and keeps a
same-boot shutdown witness. If a job was already stopped and no witness exists,
it cannot safely make that claim. Removing `~/ods` does not remove the protected
native services and may leave them trying to restart against missing files.

The reboot procedure preserves the six verified native launchd plists outside
`/Library/LaunchDaemons`, unloads their jobs, and requires a different kernel boot
session before completing retirement. A Docker restart, logout, or reopening
Terminal does not satisfy this barrier. A vanished PID or a fabricated receipt
does not satisfy it either.

## Preconditions

- Use a reviewed source checkout containing this helper, separate from the
  installation. Commands below assume it is at `~/src/ODS`, with installation
  `~/ods`. Use the **original** installation path and installing owner; moving
  the source clone does not change protected deployment ownership.
- Keep the surviving system records, plists, runtime directories, Docker
  configuration and data unchanged. Missing owner preparation receipts alone
  do not prevent this procedure, but missing or inconsistent protected records
  do. It will not reconstruct lost application data or credentials.
- Open Docker Desktop and wait for its engine. The helper validates the exact
  Docker transport recorded by the old gateway, not an arbitrary shell context.
- A pending access/model/runtime transition, foreign state, changed service
  definitions or unverifiable ownership remains a refusal. Do not override it.

## Procedure

Run from the installing owner's ordinary Terminal. Only the helper uses sudo.
First confirm the existing refusal without changing services:

```bash
sudo /usr/bin/python3 -I "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-uninstall.py" \
  --install-dir "$HOME/ods" --owner "$(id -un)" --validate-only
```

If the result is the stopped-job/witness refusal and a maintainer has reviewed
the retained deployment, explicitly prepare recovery:

```bash
sudo /usr/bin/python3 -I "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-uninstall.py" \
  --install-dir "$HOME/ods" --owner "$(id -un)" --prepare-stopped-recovery
```

Expected: `status: reboot-required`, `phase: awaiting-reboot`, `retired: false`.
Pixel is now intentionally unavailable. Application data, models and protected
installation records remain. Original plist bytes are preserved in the private
`/private/var/lib/ods-pixel-access/retirement-plists` directory; the root-owned
`retirement.json` binds their authority and the old boot session. The helper does
not change persistent launchctl enable/disable overrides.

Save other work and restart the **Mac** through its normal Restart command.
After signing in, start Docker Desktop and wait for the engine. Then validate:

```bash
sudo /usr/bin/python3 -I "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-uninstall.py" \
  --install-dir "$HOME/ods" --owner "$(id -un)" --validate-only
```

Only after it returns `status: validated`, explicitly finish native retirement:

```bash
sudo /usr/bin/python3 -I "$HOME/src/ODS/ods/installers/macos/lib/pixel-native-uninstall.py" \
  --install-dir "$HOME/ods" --owner "$(id -un)" --resume-stopped-recovery
```

Expected: `status: retired` and a private archive path. This runs the existing
native retirement, including its scoped sandbox handling and archive retention
policy. The quarantined plists and reboot witness move into that archive with
the old deployment. The dedicated Operations identity is retained as before.
This is **not** completion of the full ODS uninstall or a new installation.
The normal ODS uninstaller can subsequently remove the remaining installation
using the intended data-retention options, followed by a fresh install.

## Interruptions and stop conditions

- `--validate-only` never prepares recovery or stops services. All three mode
  flags are mutually exclusive; normal retirement does not silently opt in.
- If preparation fails, retain everything. The journal is saved before any
  plist move. A reviewed retry of `--prepare-stopped-recovery` can continue a
  partial staging operation against identical authority. Some jobs may already
  be unloaded. Do not restore plists, create a witness or delete the archive.
- If the Mac restarted during incomplete staging, finishing preparation requires
  another restart. An incomplete journal is not evidence of a reboot barrier.
- Repeating successful preparation in the same boot does not repeat stop
  commands or reset the barrier. After reboot, use resume instead.
- If any job reappears, a plist is restored or duplicated, authority changes,
  or the transport/ownership checks fail, stop and share only the refusal code.
  Never post private JSON contents, `.env`, keys or full service definitions.

## Validation boundary

Automated tests exercise real temporary file moves and the full retirement
orchestration with fixture launchd, Docker, protected custody and kernel boot
identities. They cover missing owner receipts, a stopped gateway, normal
retirement, interrupted preparation, a second boot, changed authority and
reappearing jobs. They do not establish actual macOS reboot behavior or recovery
of a particular user's machine. A privileged, disposable-host rehearsal of both
phases and subsequent reinstall remains required before release.
