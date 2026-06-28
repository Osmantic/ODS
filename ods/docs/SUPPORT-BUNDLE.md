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
