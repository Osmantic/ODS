# Vikunja — local project planning

Set `VIKUNJA_SERVICE_SECRET` to a persistent random secret (at least 32 random bytes), install and enable in ODS, then visit `http://localhost:11031/`. Register your account, create a project and add tasks. After creating the required accounts, set `VIKUNJA_SERVICE_ENABLEREGISTRATION=false` and recreate the service to close public registration. Existing accounts remain usable; disabling registration is not disabling login.

`VIKUNJA_SERVICE_PUBLICURL` must match the actual browser URL, including port and trailing slash. If you change `VIKUNJA_PORT`, update that URL too. Using `127.0.0.1` in the browser while configuring `localhost`, or another mismatched origin, can cause authentication/CORS problems. Do not solve that by disabling authentication or CORS.

The workspace supports tasks, project organization, deadlines and file attachments. This is separate from the Portal's task list; installing Vikunja does not silently copy chat goals or claim to synchronize them. API integrations need explicit credentials and project mapping.

## Deployment and storage

- Official AGPL-3.0 2.6.0 image pinned by digest. The Linux image supports amd64 and ARM variants, including arm64; Windows/macOS use Linux containers through Docker Desktop, and Linux uses Docker Engine. No GPU or specific chat model is involved.
- The upstream runtime is scratch-based. A small build stage prepares database/attachment directories, then copies them with UID/GID 1000 ownership into the final image. Runtime stays nonroot, with no shell, host filesystem or Docker socket added.
- `vikunja-db` stores the SQLite database at `/db/vikunja.db`. `vikunja-files` stores attachments at `/app/vikunja/files`. Fresh named volumes inherit image ownership, avoiding host-specific `chown` instructions. Previously created volumes with wrong ownership need an explicit migration; rebuilding does not change existing volume ownership.
- SQLite suits a local or small-team workspace. For a larger deployment, plan a supported database migration; changing an environment value does not transfer the existing tasks. The 1 GB container limit is a starting allocation, not a throughput guarantee.
- The UI binds to loopback only. All browser/API traffic uses the same service. Keep the signing secret when restarting/restoring; replacing it can invalidate sessions and signed credentials. Back up both volumes and configuration together, with writes stopped for a consistent SQLite copy. Disabling preserves volumes.

## Account and feature boundaries

Email sending and email reminders are disabled because no SMTP service is configured. Due dates still exist, but email delivery is not promised. Configure a real mail service deliberately if required. Error-reporting telemetry is disabled for both backend and frontend. Remote access requires a matching HTTPS URL and an intentional reverse-proxy configuration.

This recipe uses the current `service.secret` environment setting; `service.JWTSecret` is deprecated in this release. There are no preset user credentials. Avoid deleting the database to recover an account: use the upstream administrative CLI/recovery procedures instead.

## Health and checks

The container uses the real `vikunja healthcheck` command, not a nonexistent shell utility. ODS uses the `/health` route. These invoke application health logic; neither proves a complete browser task/attachment flow.

Image manifests, runtime Dockerfile, configuration keys and health implementation were inspected. Schema/Compose and installation-staging checks remain distinct from application testing. Docker image build, registration, task creation, attachment upload/download and persistence after restart still need runtime validation. A useful first check is to create a project with two tasks, complete one, attach a small file and verify all three states after restarting.

- [Release source and license](https://github.com/go-vikunja/vikunja/tree/v2.6.0)
- [Official runtime image](https://github.com/go-vikunja/vikunja/blob/v2.6.0/Dockerfile)
- [Configuration implementation](https://github.com/go-vikunja/vikunja/blob/v2.6.0/pkg/config/config.go)
- [Health command](https://github.com/go-vikunja/vikunja/blob/v2.6.0/pkg/cmd/healthcheck.go)
