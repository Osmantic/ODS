# Dagu: local YAML jobs

Dagu 2.16.6 executes native YAML dependency graphs and records run status and
step logs. This recipe uses local execution inside one non-root container,
with native builtin authentication and a persistent data directory. It does not
convert n8n flows or provision external workers, databases or cloud accounts.

## Enable

From `ods/` on Linux, prepare the data directory:

```bash
sudo install -d -m 0700 -o 1000 -g 1000 data/dagu
```

In Extensions, supply `DAGU_ADMIN_PASSWORD` (at least eight characters; use a
long unique password) and a distinct long random `DAGU_TOKEN_SECRET`. Enable
Dagu and open `http://localhost:8525`. Sign in as `ods`. `DAGU_PORT` changes
the host port; `BIND_ADDRESS` defaults to loopback. Trusted ODS clients use
`http://dagu:8080/api/v1` and a native bearer token or API key.

Create a YAML job through the UI/API, then start it and inspect its run receipt.
For example, this job writes a local artifact before consuming it:

```yaml
name: local-artifact
type: graph
steps:
  - name: produce
    command: sh -c 'printf "local artifact" > /var/lib/dagu/artifact.txt'
  - name: consume
    command: cat /var/lib/dagu/artifact.txt
    depends: [produce]
```

Use `POST /api/v1/dags/<name>/start` for new API integrations, then consume
native run status or SSE updates. Persist the returned run ID. A failed step is
a failed run receipt; it must not be interpreted as success merely because the
HTTP request to start it succeeded. Reusing an existing run ID is rejected.

## Authentication and runtime boundaries

- **Community edition:** this pinned image supports the initial administrator
  and up to two native API keys. Managing additional interactive users requires
  Dagu Pro. This recipe does not provision a license or claim free multi-user
  management. Native API keys can use `viewer` (read receipts) or `operator`
  (execute existing jobs) roles; neither grants DAG editing or user management.
  Grant only the `rest_api` surface when using HTTP automation.
- Bootstrap admin variables apply only when no users exist. Rotate an existing
  password through Dagu's native account/password API; editing the seed setting
  alone does not change the stored account. Keep the ODS value current for any
  future empty-directory initialization. JWTs expire after one hour. Revoke API
  keys separately; do not assume password rotation revokes independently issued
  keys. Retain the JWT signing secret across restores.
- Job authors execute commands inside the container and can read its data and
  environment, including Dagu configuration secrets inherited by native jobs.
  They are trusted operators. The disabled web terminal is not a shell sandbox.
  No Docker socket or host workspace is mounted. Container/SSH/cloud steps need
  separately reviewed access and are outside this local recipe's qualification.
- Native examples, the distributed coordinator, tunnel, Git sync and web
  terminal are disabled. Metrics require native authentication. The scheduler
  remains active for jobs operators explicitly schedule; no schedule is seeded.
  No external notification or AI provider is configured.
- The image's Tini remains PID 1. Explicit UID/GID 1000 replaces the image's
  root/sudo UID-adjustment wrapper. Root filesystem writes and extra capabilities
  are disabled; owned data and 128 MiB `/tmp` remain writable. The 2 CPU / 1 GiB
  container and 512 MiB Go-memory target bound the whole local service budget,
  not individual subprocess guarantees or measured throughput.

## State and recovery

Keep the complete `data/dagu` directory: YAML definitions, run histories and
logs, scheduler/queue state, authentication data and local job artifacts all
belong together. Stop Dagu gracefully before copying it and the configuration
secrets to a private backup. Restore into a separate empty directory with UID/GID
1000 ownership using the pinned image, then verify login, both successful and
failed run receipts, step logs and the required local artifacts. An in-flight
command is not guaranteed to survive interruption or replay without side effects.
Native log/history retention is not an artifact backup policy or a disk quota.

Disable the extension to stop it while retaining data. Before upgrading, take
a cold backup; rollback uses that backup and its matching image, rather than
an in-place downgrade of potentially migrated state.

## Qualification

```bash
python -m pytest ods/tests/test_dagu.py -q
sudo --preserve-env=PATH env ODS_TEST_DAGU=1 python -m pytest ods/tests/test_dagu.py -q -s
```

Linux amd64 native tests create and execute a real two-step YAML graph, verify
Unicode artifacts and failed dependency behavior, reject duplicate run IDs,
exercise viewer/operator API-key boundaries and revocation, rotate the native
admin password, recreate and cold-restore completed run history. The fixture uses
the pinned server's retained **deprecated** `/start-sync` endpoint to receive a
bounded completion receipt without custom polling; new clients should use the
`/start` contract described above. Browser interaction, SSE clients, schedules,
interrupted-run recovery, remote workers, paid user management, ARM/macOS/Windows
runtime, load and cross-version migration remain unqualified.

References: [builtin authentication](https://docs.dagu.sh/server-admin/authentication/builtin),
[2.16.6 API contract](https://github.com/dagucloud/dagu/blob/v2.16.6/api/v1/api.yaml),
[2.16.6 configuration bindings](https://github.com/dagucloud/dagu/blob/v2.16.6/internal/cmn/config/loader.go).
