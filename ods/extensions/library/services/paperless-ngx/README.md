# Paperless-ngx

Document management system that transforms physical documents into a searchable online archive. Automatic OCR, tagging, classification, and full-text search for PDFs and images.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable paperless-ngx
ods disable paperless-ngx
```

Your data is preserved when disabling. To re-enable later: `ods enable paperless-ngx`

## Access

- **URL:** `http://localhost:7807`

## First-Time Setup

1. Enable the service: `ods enable paperless-ngx`
2. Open `http://localhost:7807`
3. Create an admin account on first launch
4. Upload your first document via the web interface or email import

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `PAPERLESS_SECRET_KEY` | Django secret key for session security (auto-generated) | _(required)_ |
| `PAPERLESS_DB_PASSWORD` | Shared application/PostgreSQL password | `paperless` |

Set `PAPERLESS_DB_PASSWORD` before the first enable to initialize PostgreSQL with
your chosen password and pass the same value to Paperless. Empty or unset values
retain the existing `paperless` default. Changing this variable after PostgreSQL
has initialized does not rotate its stored password: update the database role
password as well before recreating the services. Keep the database data directory
when changing credentials or disabling the extension.
