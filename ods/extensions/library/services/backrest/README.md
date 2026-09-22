# Backrest — explicit project backups with Restic

Backrest 1.14.1, GPL-3.0, official image pinned by digest. Upstream: https://github.com/garethgeorge/backrest/tree/v1.14.1. This image already includes Restic; `BACKREST_RESTIC_COMMAND=/bin/restic` selects that bundled version rather than automatically downloading a changing binary at runtime.

## First setup

Open `http://localhost:11108/` and create your user/password using the native first-run setup. Do not leave authentication disabled. This recipe does not inject a preset browser account, initialize a repository, create a schedule, send notifications or run a backup automatically.

Create a Restic repository in the UI, explicitly choosing its destination and encryption password. Keep that password independently: losing it can make snapshots unrecoverable. A local destination can be `/repos/project-name`. Remote S3/SFTP/rclone backends require the actual endpoint and credentials supplied by the owner; none is connected implicitly. Remote storage is a separate service, not something included in the container.

## Select the real source

By default the container has an empty `/sources` staging volume, not access to your computer's home folder. You can copy explicitly selected project files into it with `docker cp "ACTUAL_PROJECT_PATH/." ods-backrest:/sources/project-name/` after creating that destination with `docker exec ods-backrest mkdir -p /sources/project-name`. Ensure copied files are readable by UID 1000 (Docker copy can preserve restrictive host permissions). Create a plan for that exact directory.

Staged copies are not live synchronization: future host changes require another explicit copy, and removed source files are not automatically removed by Docker copy. For ongoing backups, deliberately replace the staging mount with a read-only bind mount of the actual selected project folder or a selected project data volume. Docker Desktop must have access to that host path on Windows/macOS. Do not mount the host root, Docker socket or unrelated credentials. ODS does not yet offer an automatic per-project mount editor, so that mapping is an explicit deployment configuration step.

Use `/restore/project-name` as an initial restore destination so recovery can be inspected before replacing working files. This recipe's staging and restore volumes are writable, not an immutable source guarantee. Review retention, prune and command-hook settings before enabling a plan; no hooks are supplied by ODS. Restic file backups do not make a live database consistent: use a proper database dump or stopped/snapshot-coordinated backup.

## Persistence and limits

Separate named volumes retain application operation data (`/data`), configuration/credentials (`/config`), cache (`/cache`), staged source files (`/sources`), local repositories (`/repos`) and recovered files (`/restore`). Protect the config volume as it can contain secrets. Back up the configuration and retain repository credentials outside this installation. Cache can be rebuilt; repository data cannot. A local repository on the same physical disk is not protection from disk failure; choose an independent destination where needed.

The app runs as UID/GID 1000 with a read-only root and a 256 MiB temporary directory. Large restore/export operations may need additional temporary capacity. Limits are two CPUs and one GiB memory. No privileged mode, Docker socket, automatic host mounts or GPU/model dependency. Native backup operations remain subject to the selected source permissions.

Only loopback port 11108 is published, but other ODS network peers can reach the service; complete authentication setup before enabling plans or adding sensitive repositories. Remote publication requires deliberate HTTPS/authentication configuration.

## Verification

Official image supports targeted Linux amd64/arm64 Docker environments on Windows/Linux/macOS. The HTTP probe checks UI availability, not backup success or restorability. Image build, first-user authentication, actual snapshot/check/restore, retention and platform runtime remain pending. No backup, repository mutation, container or model was run during preparation.
