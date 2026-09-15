# Readeck: local reading library

Install **Readeck (Local Reading Library)** from Extensions, set a unique
`READECK_PASSWORD`, then enable it. Open `http://localhost:8096` and log in as
`ods`. `READECK_PORT` changes the loopback host port; `BIND_ADDRESS` does not
expose this recipe to the LAN.

The pinned Readeck 0.23.2 image saves articles, labels and reading state locally
and provides native HTML, Markdown and EPUB exports. Saving a URL deliberately
makes requests to that website and its resources. This is not a blanket network
sandbox: only submit URLs you intend this local service to fetch. Some external
media, especially videos, can require their source after capture.

## Startup and account state

Use `openssl rand -hex 24` to generate a password. Startup requires 16–128
ASCII letters, digits, `_` or `-`; invalid values stop before initializing
application state and are not printed. The native CLI reads the bootstrap
password from an environment reference, so the plaintext is not passed in the
CLI's arguments. Docker/ODS administrators can still read the source environment.
The running server drops that bootstrap environment variable after initialization.

The recipe creates the `ods` administrator **only when both** `config.toml` and
`data/db.sqlite3` are absent. Once they exist, it preserves the database account,
password, permissions and native configuration. A native dry run confirms the
`ods` account exists before starting HTTP. Changing `READECK_PASSWORD` and
recreating an existing instance does not reset its account password.

Keep the `ods` account while using this recipe; renaming/deleting it stops the
next startup with an explicit recovery message. Additional users remain native
Readeck accounts. To rotate the current password, use Profile → Password with
the current password, verify a fresh login, and maintain an appropriate bootstrap
value for a future clean installation. Password changes revoke older browser
sessions; the initiating session remains logged in. API tokens have a separate
lifecycle and must be disabled or deleted through native token management.

Use Profile → API Tokens to create an application token. Limit its roles to
`bookmarks:read` for a reader. Send `Authorization: Bearer <token>` to the native
API. A Basic-auth password must be an API token too; the normal account password
is for the login form. The real test covers token creation, read-only scope and
explicit revocation. Native browser requests retain Go's cross-origin protection.

## Storage and access

`data/readeck` contains the generated native configuration/keys, SQLite database
and saved article resources. ODS prepares it for UID/GID 1000. The container runs
with a read-only filesystem, 128-MiB temporary storage, dropped capabilities,
two CPUs and 1 GiB memory. Docker's init process handles child reaping. Two
background workers are configured. Only this application directory is mounted;
ODS models, configuration and host home directories are not exposed.

Allowed HTTP hosts are `localhost`, `127.0.0.1` and `readeck` for the ODS network.
Only container-loopback addresses are trusted as reverse proxies. This recipe
uses local HTTP; it does not configure a remote proxy, TLS, email delivery,
federated login or remote access. Administrators and native public-share actions
remain trusted operator decisions.

The native `readeck healthcheck` tests the TCP listener. ODS separately requests
`/login`; neither probe alone proves every saved article can be read. The live
fixture exercises authenticated capture, export and restored reads.

## Backup, incomplete state and recovery

Disable the service before copying its entire `data/readeck` directory to a
separate backup destination. Preserve ownership and permissions. Restore the
complete directory with the same pinned image, then verify login and saved
article exports. Disabling/removing the extension retains the data. Keep a
compatible cold backup before upgrading; an image downgrade alone does not undo
a database migration.

If only the configuration or database exists, startup refuses to regenerate the
missing half. Restore both from the same backup. This preserves native encryption
keys and avoids accidentally presenting a fresh setup flow over incomplete state.
If initialization was interrupted after creating a database but before creating
`ods`, startup also refuses to expose onboarding. With the extension disabled,
recover that account explicitly using the installed Compose definition. From the
ODS installation directory:

```bash
docker compose --project-directory . \
  -f data/user-extensions/readeck/compose.yaml \
  run --rm -T --no-deps --entrypoint /bin/readeck readeck \
  user -config config.toml -user ods -email ods@localhost.invalid \
  -group admin -password env:READECK_PASSWORD
```

This recovery command also changes the password if `ods` already exists, so use
native profile settings for routine password changes. Re-enable and verify login.
The tests qualify recovery against the same native database without deleting it.

## Qualification

```bash
python -m pytest tests/test_readeck.py -q
ODS_TEST_READECK=1 python -m pytest tests/test_readeck.py -q
cd extensions/services/dashboard-api
pytest tests/test_readeck_install.py tests/test_extensions.py tests/test_host_agent.py -q
```

The live test uses the exact image and its BusyBox HTTP server as an isolated
article source. It checks login/cross-origin denial, native API token creation,
URL capture, Unicode text, labels, full-text search, HTML/Markdown/EPUB exports,
read-only token permissions, source shutdown, recreation with a changed seed,
native password rotation, cold restore and token revocation. EPUB content is
resolved through its package manifest. Additional real-image cases exercise
missing-account/missing-configuration recovery. The ODS install test verifies
authenticated definition installation and host ownership preparation.

Interactive browser rendering, arbitrary websites/media, large libraries,
email/OIDC/TOTP, native macOS/Linux hosts, upgrades and a full network-egress
assessment remain unqualified. Runtime evidence covers Linux/amd64 Docker Desktop
on WSL Ubuntu. This recipe preserves native application contracts and does not
claim an audit of every upstream feature.

Native contracts: [0.23.2 user CLI](https://codeberg.org/readeck/readeck/src/tag/0.23.2/internal/app/user.go),
[bookmark creation](https://codeberg.org/readeck/readeck/src/tag/0.23.2/docs/api/bookmarks/doc-create.md),
[profile/password/token routes](https://codeberg.org/readeck/readeck/src/tag/0.23.2/internal/profile/views.go).
