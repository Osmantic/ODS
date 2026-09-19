# Baserow

Open-source no-code database tool and Airtable alternative. Create databases, tables, and views with a spreadsheet-like interface — self-hosted with no storage restrictions.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable baserow
ods disable baserow
```

Your data is preserved when disabling. To re-enable later: `ods enable baserow`

## Access

- **URL:** `http://localhost:3007`

Set `BASEROW_PORT` to change the published port. The default public URL follows
that port, for example `BASEROW_PORT=8307` advertises `http://localhost:8307`.
Set `BASEROW_PUBLIC_URL` explicitly when using a hostname, another device or an
HTTPS reverse proxy; that complete URL takes precedence over the local default.
Changing the public URL does not configure TLS or change the bind address.
Recreate the service after changing its environment. Existing explicitly set
public URLs are retained and must be updated separately if their origin changes.

## First-Time Setup

1. Enable the service: `ods enable baserow`
2. Open `http://localhost:3007`
3. Create an admin account on first launch
4. Start building databases with the spreadsheet interface
