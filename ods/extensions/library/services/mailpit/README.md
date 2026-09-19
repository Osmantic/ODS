# Mailpit — Mock SMTP Server & Email Inspection Web UI

An optional, lightweight mock SMTP server and email inspection dashboard. Mailpit catches all outgoing emails sent by local web apps, background workers, and automation platforms (such as n8n, Flowise, Paperless-ngx, or Django), displaying them in an intuitive web UI without delivering messages to real inboxes.

## Setup

Enable **Mailpit (Email Testing & Web UI)** via Extensions.

Once enabled:
- **Web UI & REST API**: `http://localhost:8025`
- **Mock SMTP Port**: `localhost:1025` (or `mailpit:1025` from containers on `ods-network`)

Configure your application's SMTP settings to:
- **Host**: `localhost` (or `mailpit` inside Docker)
- **Port**: `1025`
- **TLS**: Disabled (Plaintext)
- **Authentication**: Any username/password (accepted automatically)

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `MAILPIT_PORT` | `8025` | Published Web UI and message inspection REST API port |
| `MAILPIT_SMTP_PORT` | `1025` | Published mock SMTP port accepting application test mail |

The default binding is loopback (`127.0.0.1`). Container privilege escalation is restricted via `no-new-privileges:true`.

## Usage & API Inspection

### Sending an Email via Python

```python
import smtplib
from email.mime.text import MIMEText

msg = MIMEText("This is an alert from an autonomous agent.")
msg["Subject"] = "Test Alert Notification"
msg["From"] = "agent@ods.local"
msg["To"] = "dev@example.com"

with smtplib.SMTP("localhost", 1025) as server:
    server.send_message(msg)
```

### Inspecting Messages via REST API

```bash
# List captured emails
curl http://localhost:8025/api/v1/messages

# Inspect server status
curl http://localhost:8025/api/v1/info
```

## Data and Persistence

Mailpit persists received test messages in a lightweight SQLite database under `./data/mailpit`. Disabling or restarting the container preserves all captured email history.

References: [Mailpit GitHub](https://github.com/axllent/mailpit), [Mailpit API Documentation](https://mailpit.axllent.org/docs/api-v1/).
