# Mailpit

Mailpit 1.31.1 captures test email for workflow development. It stores messages locally and provides an inbox and HTTP API. This recipe configures no SMTP relay, forwarding, webhook, or automatic connection to an existing workflow. Captured messages are not delivered to their recipients.

## Enable

Set two different random passwords in `.env`, using 16-128 letters, digits, underscores or hyphens, then install Mailpit from the Extensions library. `openssl rand -hex 24` generates a suitable password. The startup guard rejects unsupported credential syntax without printing its value.

| Variable | Purpose | Default |
| --- | --- | --- |
| `MAILPIT_PORT` | Inbox and HTTP API on host loopback | `8025` |
| `MAILPIT_SMTP_PORT` | SMTP capture on host loopback | `1025` |
| `MAILPIT_UI_PASSWORD` | Required inbox/API password, username `ods` | none |
| `MAILPIT_SMTP_PASSWORD` | Required SMTP password, username `ods` | none |

Open `http://localhost:8025`. Configure an explicitly selected test workflow to use SMTP host `mailpit`, port `1025`, username `ods` and the SMTP password when it shares `ods-network`. A host process uses `127.0.0.1` and `MAILPIT_SMTP_PORT`. SMTP authentication uses PLAIN/LOGIN without TLS; these listeners are intended for the trusted local host and Docker network. Both published ports remain loopback-only even when ODS uses a broader `BIND_ADDRESS`.

The inbox password also protects message creation through `/api/v1/send`; the SMTP password alone cannot read the inbox. The readiness endpoint is public and reports availability, without exposing message contents. POP3 is disabled. No workflow credentials are modified automatically.

## Persistence and limits

The host agent synchronizes `config/mailpit/entrypoint.sh` and prepares `data/mailpit` for UID/GID 1000 using the existing installation boundaries. The process runs without root privileges, with a read-only root filesystem and all Linux capabilities dropped. The SQLite database and its WAL files live in `data/mailpit`.

Mailpit periodically prunes messages beyond 500 messages or seven days; it is a test inbox, not an email archive. Individual messages are limited to 10 MB and 20 SMTP recipients. Defaults allow one CPU and 512 MiB memory, without a throughput guarantee.

Disable/re-enable and recreation retain the database. Rotate either password in `.env` and recreate the container, then update its clients. Rotation does not erase captured messages. Credentials remain visible to `.env` and Docker configuration administrators; the native process environment contains its authentication credentials.

For a consistent backup, stop the service and copy the entire data directory with its ownership. Restore with the same pinned image before considering an upgrade. Do not assume downgrade compatibility. Removing the extension preserves the data until explicitly purged.

## Privacy and qualification

Automatic version checks and reverse DNS lookups are disabled. Remote CSS/fonts in messages are blocked, but remote images, external links, and user-triggered link/HTML checks can still access the network. The Docker network is not an egress firewall. Use synthetic email and avoid activating those features when testing offline behavior.

The opt-in `ODS_TEST_MAILPIT=1` test covers the pinned image's authenticated SMTP -> persisted inbox/API path, attachments, rejected access, recreation and credential rotation on Linux/amd64. It does not qualify real recipient delivery, TLS, native Windows/macOS installation, sustained load, or a separate backup restore.

Upstream: [version 1.31.1](https://github.com/axllent/mailpit/releases/tag/v1.31.1), [SMTP authentication](https://mailpit.axllent.org/docs/configuration/smtp/), [HTTP authentication](https://mailpit.axllent.org/docs/configuration/http/), [storage](https://mailpit.axllent.org/docs/configuration/email-storage/).
