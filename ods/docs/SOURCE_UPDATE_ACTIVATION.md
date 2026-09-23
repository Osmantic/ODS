# Verifying update activation

`ods-update.sh update` is for a Git-backed source checkout. It requires Bash 4+
and Docker Compose v2 before making a snapshot or changing source. On macOS,
invoke it with the modern Bash installed for `ods-cli`, rather than `/bin/bash`.
Ordinary installed runtime updates continue to use `ods update`.

Source updates now validate the active Compose files and checked-out branch,
create a configuration snapshot, pull fast-forward-only, build local service
images, run migrations, restart containers and restart the host agent. A failed
snapshot, Git fetch/pull or build does not stop the running stack. Operational
Compose failures are not retried through a different Compose implementation.
Quoted Compose paths remain single arguments.

Completion is recorded only after agent activation and configured-service checks.
The service check includes missing containers, stopped containers, unhealthy
replicas and the HTTP endpoints of configured dashboard/llama services. It uses
the published bind address and ports, including IPv6 and wildcard binds.

A command container may finish successfully instead of remaining running only
when its Compose service explicitly declares both `restart: "no"` and
`labels: {com.ods.lifecycle: oneshot}`, and every observed container exited zero.
The Aider installation check declares this contract. A name, empty health URL or
`startup_check: false` alone does not establish successful completion.

`ods agent restart` propagates service-manager failures, refuses managed units
whose effective command/configuration targets another installation, waits for
the previous endpoint to stop, and requires readiness after starting. The
launchd check compares the loaded arguments as well as the plist on disk.
The macOS private Python virtual environment is supported. Ordinary `ods update`
also returns a failure and keeps the prior recorded version when agent activation
fails after container changes; it reports that partial state explicitly.

## Recovery limits

The automatic snapshot restores configuration only. It does not reset Git,
restore old built images, reverse migrations or guarantee data rollback. The
failure output states this limit. Inspect the reported error and runtime state
before retrying; a failed update may already have changed source or containers.

Service/process identity checks are operational ownership checks, not isolation
against another process with the same user privileges. Real platform service
managers and clean installations still require release qualification.

## Local evidence

On 2026-09-23, WSL passed 29 source-update cases, 17 lifecycle cases, the seven
existing session/installer lifecycle cases, both CLI-update regressions, and the
backup/rollback tests including eight restore-atomicity cases. Source-update
tests execute the full updater with real temporary configuration copies and
version writes. Git, Docker, HTTP and service management are simulated; no
running ODS services, model downloads or native macOS installation were touched.

Reproduce the main regression checks from the repository root:

```sh
bash ods/tests/test-update-compose-restart-contract.sh
bash ods/tests/test-agent-lifecycle-failures.sh
bash ods/tests/test-agent-restart-nonfatal.sh
bash ods/tests/test-cli-update-verification.sh
bash ods/tests/test-ods-cli-update-verification.sh
bash ods/tests/test-update-rollback-restore-failure.sh
```

The source updater's Windows-native test run explicitly skips its POSIX cases;
WSL results are not evidence of native Windows or macOS service activation.
