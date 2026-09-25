# Kanboard for ODS

Kanban boards, tasks, subtasks, swimlanes, time tracking and configurable automatic
actions. This MIT application stores its own project management data. It does not
replace Portal goals or claim to import the current workspace automatically.

## Installation

Install `kanboard`, set `KANBOARD_INITIAL_PASSWORD` to 12–72 UTF-8 bytes, and open
`http://localhost:11063`. Sign in as `admin` with that password. `KANBOARD_PORT`
changes the loopback host port. Create your project, columns and tasks in the UI.

A version-checked patch changes the initial SQLite migration's default admin hash
to use your required password. The wrapper validates it and runs upstream
`db:migrate` before starting HTTP, so PHP-FPM environment filtering cannot cause
an empty initial password. Existing user rows and changed passwords are preserved;
the initial password setting is not a password-reset mechanism. Imported databases
retain their existing accounts and credentials. This recipe supports SQLite only.

## Persistence and services

`kanboard-data` contains the SQLite database and attachments. `kanboard-plugins`
and `kanboard-tls` preserve upstream plugin files and internally generated TLS
files. The native supervisor requires root for setup, ownership and nginx/PHP
management; application workers retain the upstream nginx identity. Port 443 is
not published and the generated self-signed certificate is not installed in the
host trust store. Only loopback HTTP is exposed.

Back up all volumes with the app stopped before upgrading. Upstream SQLite
migrations run on startup, so keep a compatible backup before changing image
versions. Do not delete volumes when simply recreating the container. There is no
host directory, Docker socket or project source mount.

The upstream plugin installer remains disabled by default. No plugins, SMTP
credentials, external authentication, webhook recipients or shared database are
preconfigured. Configure these deliberately through Kanboard's documented settings
if needed. No GPU, model weights or inference server is required.

Official image v1.2.54 publishes amd64 and ARM variants. Use Docker Engine on
Linux, or Docker Desktop Linux containers on Windows/macOS. Actual OS execution
and browser workflows remain unverified.

## Verification boundary

HTTP health uses the native healthcheck.php endpoint. Configuration checks do not
prove login, migration recovery, attachments or board behavior. PHP execution,
image build, first install/login, board operations and backup/restore remain
runtime-pending. No application containers were started during integration.

Source: https://github.com/kanboard/kanboard/tree/v1.2.54
