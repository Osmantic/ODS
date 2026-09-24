# Gitea (Git Hosting)

Self-hosted lightweight Git server with code review, issue tracking, CI/CD, and wiki — a GitHub/GitLab alternative that runs on minimal resources.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable gitea
ods disable gitea
```

Your data is preserved when disabling. To re-enable later: `ods enable gitea`

## Access

- **URL:** `http://localhost:7830`
- **SSH:** `ssh://git@localhost:2222/<user>/<repo>.git`

## First-Time Setup

For a new installation, enable with `ods enable gitea`. A one-shot initializer
sets the two mounted directory owners and an existing `app.ini` owner to the
image's UID/GID 1000; the
application runs as the image's non-root user. It does not recursively change
existing files. Custom ownership layouts need operator review.

The recipe sets `INSTALL_LOCK=true` and disables public registration, so there
is **no setup wizard or default administrator**. Create the first administrator
through the container CLI, using your own strong password:

```bash
docker exec -it ods-gitea gitea admin user create --help
```

Use the displayed `--username`, `--email`, `--password` and `--admin` options.
Enter the command in a private terminal; command arguments can be visible to
local process inspection and shell history. Then sign in at the configured web
URL and create a repository. Do not enable public registration to bootstrap an
existing private instance.

## Existing installations: preserve configuration before updating

The old recipe persisted `/var/lib/gitea` but left `/etc/gitea` in an anonymous
Docker volume supplied by the rootless image. `compose up --force-recreate` may
reuse that volume, whereas `down` followed by `up` can create a new one. Losing
`app.ini` can regenerate instance secrets even though the database/repositories
remain. Copy the original configuration **before** replacing/removing the old
container or enabling the updated recipe:

```bash
# Run from the ODS installation root, while the old container still exists.
docker stop ods-gitea
mkdir -p ./data/gitea-config
docker cp -a ods-gitea:/etc/gitea/. ./data/gitea-config/
```

Do not overwrite an existing `gitea-config/app.ini` without comparing its origin.
Verify the copied `app.ini` without publishing its contents; it contains secrets.
The initializer normalizes its owner to UID/GID 1000 while preserving its bytes
and file mode, because host copies can receive the copying user's ownership.
Take a cold backup of **both** `data/gitea` and `data/gitea-config` together before
applying the updated recipe. Rootless Docker, Windows bind mounts, symbolic
configuration files and custom UIDs require host-specific review.
If the old container is already gone, identify its original anonymous volume
from your deployment records/backups before proceeding. A new blank configuration
is not a recovery of the old instance.

After activation, verify the same administrator, private repositories and instance
configuration survive `down`/`up`. Keep the cold backup until this passes. Rollback
must restore the paired database/data and configuration to the same image version;
retain the explicit `/etc/gitea` mount rather than reverting to anonymous state.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `GITEA_HOST` | Hostname for Gitea server | `localhost` |
| `GITEA_PORT` | External port for web interface | `7830` |
| `GITEA_SSH_PORT` | External port for SSH access | `2222` |
| `GITEA_APP_NAME` | Display name for the instance | `ODS Git` |
