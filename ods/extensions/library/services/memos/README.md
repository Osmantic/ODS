# Memos — local notes and workflow results

Memos provides Markdown notes, tags, attachments and a personal-access-token
API. This optional recipe pins **0.30.0** by image digest and keeps SQLite plus
local attachments under `data/memos`. It needs no GPU or cloud account. It does
not automatically ingest Pixel conversations, run an LLM, or treat saved text
as instructions for an agent.

## Install and initialize

Install Memos from the ODS Extensions library. For a manual install, run from
the ODS installation directory:

```sh
cp -R extensions/library/services/memos extensions/services/memos
ods enable memos
```

Open `http://localhost:8099` on the ODS host and create the first account. That
account becomes the administrator; there is no shared default password. Keep
the default loopback binding during initialization. Additional anonymous
registration is disabled by the mounted `memos-instance-setting-general.json`;
the administrator can create further accounts. The whole GENERAL settings
group is deployment-managed, so UI/API edits to that group are rejected by
Memos. Edit the installed JSON and recreate the container to change it.

`MEMOS_PORT` in ODS `.env` changes only the published host port. The service,
health probe and container-to-container API stay on **5230**. The empty
`MEMOS_INSTANCE_URL` selects Memos' private access mode. This recipe does not
enable anonymous note browsing or private-network webhooks. Use private memo
visibility for personal notes. Deliberately publishing notes or exposing the
service through a proxy requires a separate operator configuration.

The pinned entrypoint starts with the four capabilities needed to repair data
ownership and switch identity, then executes Memos as UID/GID **10001**.
The image filesystem is read-only; `/tmp` and the data directory are writable.
Do not change the data ownership or runtime UID casually on an existing install.

## Save results from a workflow

Create a personal access token in Memos settings with an appropriate expiration.
In an n8n HTTP Request node on `ods-network`, use:

- Method: `POST`
- URL: `http://memos:5230/api/v1/memos`
- Authentication: an n8n Header Auth credential named `Authorization`, with
  value `Bearer <your Memos personal access token>`
- JSON body:

```json
{"content":"#research\nA result reviewed before saving.","visibility":"PRIVATE"}
```

Store the token in the workflow credential store, not in an exported workflow
or prompt. A successful response contains a `name`, such as `memos/<id>`;
read it with authenticated `GET /api/v1/memos/<id>`. The token carries its
user's permissions. Revoking it in Memos disables subsequent requests.

## Persistence, backup and rollback

`/healthz` proves the process answers; it does not prove a user can log in or
write a note. Check those actions after an upgrade. Recreating the container
retains accounts, tokens, notes and attachments through the bind mount.

For a cold backup, stop Memos and copy the entire data directory before starting
it again. Docker can copy from a stopped container:

```sh
ods stop memos
backup_dir="memos-backup-$(date +%Y%m%d-%H%M%S)"
mkdir "$backup_dir"
docker cp ods-memos:/var/opt/memos/. "$backup_dir/"
ods start memos
```

Retain the image digest and installed configuration with the backup. Upgrades
may migrate SQLite; rollback requires the previous image **and** its matching
pre-upgrade data, restored while the service is stopped. Do not run an older
image against a database migrated by a newer release. `ods disable memos`
stops the service; keep `data/memos` until its contents are no longer needed.

## Validation scope

The opt-in `ODS_TEST_MEMOS_DOCKER=1 pytest -q tests/test_memos_extension.py`
test creates an isolated Compose project, bootstraps an administrator, rejects
another anonymous signup, creates a token and private note, rejects anonymous
reads, recreates the container and verifies the same token/note/login. It uses
unique container/network names and an ephemeral loopback port. Compose 2.24.4+
is required for the test-only port override.

Runtime verification covers Linux amd64 under Docker/WSL. The pinned registry
index also advertises arm64 and arm/v7; native Apple Silicon, rootless Docker,
Windows bind-mount ownership, backup restoration and external integrations
have not been exercised here.

Upstream references for this exact version: [deployment configuration](https://github.com/usememos/memos/blob/v0.30.0/docs/configuration-provisioning.md),
[API definitions](https://github.com/usememos/memos/tree/v0.30.0/proto/api/v1),
and [entrypoint](https://github.com/usememos/memos/blob/v0.30.0/scripts/entrypoint.sh).
