# Mailpit for ODS

Development email capture with HTML/text inspection, message headers, search
and an API. Messages are kept locally instead of delivered to their addressed
recipients. This is a testing inbox, not a production mail server.

## Credentials and application connection

Set `MAILPIT_UI_AUTH` and `MAILPIT_SMTP_AUTH` before enabling, each in upstream
`username:password` format. Choose distinct development credentials. Multiple
credential pairs can be separated with spaces; do not put whitespace inside a
username or password. Use plain generated passwords for this environment form,
not a production mailbox password. No default login or accept-any mode is used.

Open `http://localhost:11052` and authenticate using your UI credentials. For an
application running directly on the same computer, configure its development
mailer as follows:

| Setting | Value |
| --- | --- |
| SMTP hostname | `127.0.0.1` |
| SMTP port | `11053` (or your `MAILPIT_SMTP_PORT`) |
| Authentication | LOGIN or PLAIN, using your SMTP credential pair |
| TLS/SSL | Off for this local testing recipe |
| Web inbox | `http://localhost:11052` |

An application on `ods-network` uses `mailpit:1025`; its own `localhost` refers
to that application container. The UI and SMTP ports are different. No existing
project, Portal account or extension is automatically redirected to this inbox.

Mailpit permits authenticated SMTP without TLS here specifically for local
development. Both published ports bind loopback. Remote clients require a
deliberate TLS and access configuration; do not expose this configuration as a
public SMTP service. POP3 is not published or configured.

## Message handling and persistence

`mailpit-data:/data` retains the SQLite database, including captured messages,
across restarts. The integration switches the official image to UID/GID 1000
after preparing the data directory. Retain that ownership on restore. Stop the
service before backing up the complete volume, including SQLite WAL state;
disabling the extension must not delete it.

Retention is capped at 1000 messages: upstream pruning removes older messages.
Each incoming message is capped at 25 MiB. This is not indefinite archival.
Export messages you need to keep and back up before version migrations.

No relay or forwarding destination is configured, and automatic relay is off.
Mailpit will not deliver captured mail to real recipients in this setup.
Optional link checks and external preview resources can contact remote sites
when used; local capture is not an assertion that all UI actions are offline.
Version checking is disabled. No SMTP message was sent during preparation.

## Runtime and validation

MIT release **v1.31.2**, official image pinned by digest, published for Linux
amd64, arm64 and 386. Windows/macOS run it through a Linux Docker engine.
Native `/mailpit` startup is preserved, with Docker init, one CPU and 1 GiB RAM.
No GPU, AI model or context setting is involved.

The native `/mailpit readyz` command checks the unauthenticated `/readyz`
endpoint, separate from protected UI/API routes. This verifies readiness, not
successful SMTP authentication or rendering. **Build, credential login, SMTP
capture, HTML inspection, retention and restore remain runtime-unverified.**
Schema/Compose/staging checks do not replace those behaviors. No containers,
real recipients or model inference were used during preparation.

- [Versioned source](https://github.com/axllent/mailpit/tree/v1.31.2)
- [Docker installation](https://mailpit.axllent.org/docs/install/docker/)
- [SMTP authentication](https://mailpit.axllent.org/docs/configuration/smtp/)
- [Runtime options](https://mailpit.axllent.org/docs/configuration/runtime-options/)
