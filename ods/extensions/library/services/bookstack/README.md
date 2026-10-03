# BookStack

Structured documentation with books, chapters, pages, revisions, role-based
permissions and a REST API. BookStack is MIT licensed; its MariaDB database is
GPL-2.0. The container packaging is maintained by LinuxServer, not BookStack.

## Before installation

Set the three declared fields in ODS configuration:

- `BOOKSTACK_APP_KEY`: `base64:` followed by the base64 encoding of 32 random
  bytes. Example generation: `openssl rand -base64 32` (add the `base64:` prefix).
  Preserve this key with your backups; do not regenerate it on restart.
- `BOOKSTACK_DB_PASSWORD`: a unique database password. At least 24 random
  alphanumeric characters avoids quoting problems in the image's config setup.
- `BOOKSTACK_URL`: the exact browser origin, normally `http://localhost:11148`.
  If you change `BOOKSTACK_PORT`, update the origin too before initialization.

Install through ODS, wait for migrations and open the service link. The upstream
initial login is `admin@admin.com` / `password`; change both immediately in the
account settings. This recipe binds to loopback by default and does not expose
the database. HTTP health is not proof that onboarding or password changes are
complete. Configure email and optional authentication in BookStack as needed.

## Storage and maintenance

`bookstack-config` stores uploaded files and application configuration;
`bookstack-db` stores the private MariaDB database. Back up both together, plus
the encryption key, before upgrades. Disabling preserves both volumes. Changing
database credentials after initialization needs a database password update;
changing only environment values will break the connection. Changing APP_URL
after content exists requires BookStack's documented URL migration procedure.

The application connects to the dedicated database on a private internal
network; no root credential is passed to BookStack. MariaDB generates its own
root password during first initialization. Do not share database initialization
logs because upstream may include that credential.

## Platforms and evidence

Both pinned image indexes support Linux amd64 and arm64. The recipe needs no GPU
or model and runs through Docker Engine or Docker Desktop on Windows/macOS.
Named volumes avoid host path/ownership assumptions. Static checks do not prove
OS-specific startup or login; those runtime checks remain pending.

- [BookStack license](https://github.com/BookStackApp/BookStack/blob/v26.05.5/LICENSE)
- [Container instructions](https://docs.linuxserver.io/images/docker-bookstack/)
- [BookStack administration](https://www.bookstackapp.com/docs/admin/)
- Exact image and platform evidence is recorded in `upstream.json`.
