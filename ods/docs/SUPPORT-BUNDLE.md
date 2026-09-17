# Support Bundle

`scripts/ods-support-bundle.sh` creates a redacted diagnostics archive that can
be attached to a GitHub issue or shared with maintainers when install, runtime,
Docker, GPU, or extension behavior is hard to diagnose from screenshots.

## Usage

```bash
# Create artifacts/support/ods-support-<timestamp>.tar.gz
scripts/ods-support-bundle.sh

# Write under a custom directory
scripts/ods-support-bundle.sh --output /tmp/ods-support

# Skip container logs
scripts/ods-support-bundle.sh --no-logs

# Print machine-readable result JSON
scripts/ods-support-bundle.sh --json
```

## What It Collects

- ODS Doctor output, when `scripts/ods-doctor.sh` can run
- Extension audit JSON from `scripts/audit-extensions.py`
- Compose resolution and compose validation output, when Docker Compose is available
- Docker version, daemon info, container summary, and short ODS container log tails
- Platform, git, disk, memory, listening port, manifest, env schema, and redacted `.env` details

Container log tails are limited to names starting with `ods-`, including bundled
sidecars such as `ods-langfuse-postgres`. Other applications whose names merely
contain `ods` are excluded from log collection and do not consume its 25-container
limit. This naming convention is not an ownership or authorization check; custom
containers outside that namespace need separate log collection. The Docker
container summary remains a host-wide diagnostic.

The command is best-effort. Missing Docker, an unreachable daemon, or a failing
diagnostic command is recorded in the bundle instead of aborting the whole run.

## Privacy

The bundle intentionally never includes raw `.env`. It writes
`config/env.redacted` instead.

The redactor masks common secret fields and headers containing words such as
`KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `PASS`, `SALT`, `AUTH`, and `CREDENTIAL`.
It also masks bearer tokens, API-key headers, and credentials embedded in remote
URLs.

Review the archive before posting it publicly. Redaction is defensive, but local
paths, hostnames, container names, model names, and non-secret configuration
values may still be useful to attackers in some environments.

Each collected diagnostic has a 60-second deadline. To allow slower hosts more time,
set `ODS_SUPPORT_COMMAND_TIMEOUT` to an integer from 1 to 3600 seconds. A timed-out
command and its child processes are stopped; the bundle retains partial output and
records exit code 124 in `manifest.json`. Collection continues with other probes.
This is a per-command limit, so the whole bundle can take longer than one deadline.
Docker availability checks also use the limit and report an unavailable daemon on failure.
