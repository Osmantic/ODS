# ODS inside WSL: owned Windows lifetime

`installers/windows.ps1` is the Linux-in-WSL installer path. It establishes a
Windows-owned lifetime client before running the Linux installer, so finishing
installation or closing the launching PowerShell/SSH session does not allow
WSL to retire an otherwise healthy Pixel service. The native Windows/Docker
Desktop installer is a separate path and does not use this helper.

Run from Windows as the Windows account that owns the WSL distribution:

```powershell
powershell -ExecutionPolicy Bypass -File .\installers\wsl-lifecycle.ps1 -Action status -Distro Ubuntu-24.04 -InstallRoot /home/ods/ods
powershell -ExecutionPolicy Bypass -File .\installers\wsl-lifecycle.ps1 -Action start -Distro Ubuntu-24.04 -InstallRoot /home/ods/ods
powershell -ExecutionPolicy Bypass -File .\installers\wsl-lifecycle.ps1 -Action stop -Distro Ubuntu-24.04 -InstallRoot /home/ods/ods
powershell -ExecutionPolicy Bypass -File .\installers\wsl-lifecycle.ps1 -Action restart -Distro Ubuntu-24.04 -InstallRoot /home/ods/ods
```

- `status` reads the Windows distribution list and the private process record.
  It does not enter WSL. Its `scope: wsl-lifetime` result reports the holder and
  distribution, **not Pixel readiness, model health, or a completed user turn**.
- `start` establishes the holder, refreshes the managed WSL NAT address and
  starts the exact installation's Compose stack as the ordinary Linux owner.
  Windows then restarts its verified host-agent unit before starting the native
  Pixel units. An existing holder is reused.
- `stop` first verifies the installation's existing private ODS ownership marker
  and native unit identities. It stops ingress, waits for gateway shutdown, stops
  the ODS auxiliaries, then stops that install's Compose stack and releases only
  its own Windows WSL client. It never enters an already stopped distribution.
  The shared operations broker and unrelated host services are not targeted.
  A service stop failure leaves the holder available for diagnosis.
- `restart` performs the same explicit stop followed by start. There is no
  automatic retry or watchdog that can undo an intentional stop.
- `release` releases only the owned lifetime client. This recovery command is
  useful after an incomplete installation that cannot run the normal stack stop;
  it does not claim to stop native services or containers. If another Windows
  client holds the distribution, those services may remain running.

The ordinary Linux owner validates the private installation marker and exact
unit copies before returning a fixed lifecycle plan. Windows accepts only the
known Pixel unit names in their required order and runs fixed
`/usr/bin/systemctl start|stop <unit>` arguments using that bound distribution's
existing root identity. This uses the Windows owner's existing
[WSL user-selection authority](https://learn.microsoft.com/en-us/windows/wsl/basic-commands#run-a-specific-linux-distribution-from-powershell-or-cmd).
It adds no sudoers rule and needs no cached sudo password. Neither Python nor
shell code from the owner's checkout is executed as root. Compose and all plan
validation remain ordinary-owner operations.

For an installed host agent, the plan also verifies the protected system unit,
Linux user and exact executable path. Windows admits only the additional fixed
`/usr/bin/systemctl restart ods-host-agent.service` command and checks that the
unit becomes active before starting Pixel. A missing host-agent unit retains the
legacy Compose-only behavior; a foreign or modified unit stops startup. This
restart also runs when the NAT address is already current, so an earlier failed
attempt cannot leave the agent listening on its old address. The internal
`ods-cli start --defer-wsl-agent-restart` option leaves this restart to the Windows
controller; direct `ods start` retains its existing Linux privilege checks.

The Windows installer resolves its Linux root from the same source directory and
path utility used by `install-core.sh`, then explicitly passes that resolved root
as `INSTALL_DIR` to the real installer command. This preserves existing
`INSTALL_DIR`, `ODS_HOME`, populated `ODS_SCRIPT_HINT`, `ODS_INSTALL_DIR`, and home
default precedence, including relative paths. Current Linux installer arguments
do not include `--install-dir`; unsupported options are not silently treated as
root overrides. Forwarded argument text is shell-quoted individually.
`--dry-run`, `--help` and `-h` do not register a persistent lifetime task.

The on-demand Scheduled Task has no trigger, no restart policy, and no execution
time limit. It runs hidden with the owner's limited interactive identity, without
storing credentials. The owner must be signed in to start it. It survives closing
the initiating terminal or SSH session; this is not a promise of service across
Windows logout/reboot. After logout/reboot, use an explicit `start`.

Each owner/distribution/Linux-root tuple has private state under
`%LOCALAPPDATA%\ODS\wsl\<identity hash>`. `instance.json` binds that tuple and the
exact task; `runtime.json` records the controller and attached client PID, UTC
start ticks, executable and command line. A changed identity is an error, not
permission to kill a reused PID. Commands serialize through a private file lock.
New files explicitly receive the current owner's SID before they are used, even
when the initiating SSH token would otherwise assign Administrators ownership.
Existing files with a different owner or a broader ACL are rejected, not reclaimed.
The controller checks only its private request file while waiting; it never
periodically launches `wsl.exe` to revive a stopped distribution.

This controller never invokes `wsl --shutdown` or `wsl --terminate`. Other WSL
distributions and their clients are left alone. Windows may naturally retire a
distribution after its final Windows client exits; keeping unrelated work alive
remains the responsibility of that work's owner.

Stale-bind recovery: an authorized `ods start` on WSL with Docker Desktop
probes the actual running container view and, when a service's bind mount has
been invalidated by a WSL restart, recreates that service from already-installed
images (no pull, no build) with real data retained and a private bounded backup
of writable data in the stale container view. Backups are capped at 1 GiB and
10,000 archive members across affected services, require disk headroom, and
remain private even when recovery fails. The helper has a 120-second total
budget within the lifecycle adapter's 300-second start budget. Paused services
are refused rather than recreated. This recovery runs before Compose during
manual starts and the saved Windows sign-in startup path;
it makes no promise of universal resilience. Real Windows reboot qualification
is still pending; the current evidence is a live laptop restoration.

For an already-managed installation missing its Windows sign-in task, repair
registration without rerunning setup or starting WSL:

```powershell
powershell -File .\installers\wsl-lifecycle.ps1 -Action enable-startup `
  -Distro Ubuntu-24.04 -InstallRoot /home/owner/ods `
  -DockerDesktopPath 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
```

Use the existing `-StateRoot` when the installation has a custom state location.
The action validates the existing owner manifest and holder task, copies the
launcher into durable private state, and preserves an explicit stopped
preference. It does not launch Docker, WSL, or the stack. An explicit start is
still required when the saved preference is stopped.

Run `ODS_WSL_RECOVERY_LIVE=1 python3 tests/test-wsl-bind-recovery-live.py` from
WSL to reproduce a replaced bind source with an isolated disposable Compose
project. The test retains the old marker in a private archive, verifies the new
container sees the replacement source, and checks repeat-start idempotence.

Qualification: the repository includes controlled Windows identity/ACL/lock and
Linux-adapter ownership/ordering tests. An isolated holder-only roundtrip has
also been verified with Windows PowerShell 5.1 and Ubuntu-24.04: real Scheduler
start, survival across initiating SSH disconnect, separate-session observation,
and release while an unrelated existing holder remained unchanged. Real Pixel
activity through stack stop/restart, Windows logout behavior, fresh installation,
and distributions with whitespace in their names still require qualification.
