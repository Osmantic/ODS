# Installer diagnostic files

Linux and macOS installers allocate each default diagnostic log inside a new
`ods-install.XXXXXXXX` directory under `TMPDIR` (or `/tmp`). The directory is
0700 and the log is 0600. The installer reports the chosen path. Diagnostics are
retained after the run; separate runs do not reuse the old shared filename.

To choose a path, set `LOG_FILE` on Linux or `ODS_LOG_FILE` on macOS. Its parent
must already exist, be owned by the current user or root, and not be writable by
other users. Ancestors must likewise be trusted; a root/user-owned sticky
ancestor such as `/tmp` is permitted above a private directory. A direct
override such as `/tmp/custom.log` is rejected. Parent aliases are resolved once
and subsequent writes use the canonical path. `/dev/null` remains an explicit
discard sink for the main diagnostic log.

Existing logs must be owned regular files with one link. The guard rejects
symlinks, hardlinks, FIFOs, devices and foreign-owned files before opening or
changing them. Safe existing logs become 0600 without truncation. During default
allocation, an owned regular legacy `ods-install.log` (Linux) or
`/tmp/ods-install-macos.log` becomes private without deleting its history. Unsafe
legacy paths are left untouched with a warning; operators should inspect them
separately. Logs from earlier versions may already have been disclosed, and a
permission change cannot undo that exposure.

The same preparation covers installer build, Compose check/launch, model-download,
SDXL download and model-upgrade diagnostics before their first write. These
checks protect against replacement by another UID through the log path; they do
not isolate the installer from a malicious process running as the same user or
as root. They also do not claim that every long-lived service's runtime log is
covered by the installer guard.

`bash ods/tests/test-installer-log-safety.sh` exercises 19 checks using disposable
fixtures, including a parent-alias swap, a FIFO timeout, foreign ownership and
replacement attempts after dropping to another UID. The last three checks require
passwordless sudo and are explicitly skipped without it. All 19 passed locally
on WSL/Linux on 2026-09-23. The three isolated contracts in
`test-phase11-local-build-failure.sh` also passed: a stale image cannot hide a
failed build, failure prevents Compose launch, and a transient build failure can
retry successfully.

The adjacent `test-bootstrap-upgrade-close-inherited-fds.sh` and
`test-systemctl-user-env.sh` passed on WSL/Linux. The former combines static
spawn-site checks with fixture lock-release checks for FDs 9 and 200, and an
isolated `systemctl` stub verifies model-upgrade cleanup with absent or supplied
user-bus variables. These fixtures neither execute the uninstaller nor contact
the host's user service manager.

The log checks include isolated macOS helper code running under Linux, not
execution on a macOS host. Native macOS filesystem/stat behavior, real service
lifecycle behavior and a real install still require separate qualification.
