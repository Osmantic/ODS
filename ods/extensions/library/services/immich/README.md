# Immich

High-performance self-hosted photo and video backup solution with AI-powered organization. Features automatic backups from mobile devices, face recognition, object detection, and automatic tagging.

## Requirements

For a new bundled PostgreSQL database, `IMMICH_DB_USER`, `IMMICH_DB_NAME` and
`IMMICH_DB_PASSWORD` configure both the application connection and database
initialization. User/database defaults remain `postgres`/`immich`. Set your own
password before first activation. The readiness probe uses the configured role
and database over TCP, so the temporary Unix-socket-only initialization server
does not count as ready. Readiness alone does not prove password authentication.

PostgreSQL initialization variables apply only to an empty data directory.
Changing them does not rename a role/database or rotate a password in existing
`data/immich/postgres`. Keep the original settings for an existing installation,
or perform a separately planned PostgreSQL migration with a verified backup.
Do not delete existing data to apply a configuration change. External databases
selected with `IMMICH_DB_HOST` remain operator-managed.

- **GPU:** NVIDIA or AMD (min 2 GB VRAM)
- **Dependencies:** None

## Enable / Disable

```bash
ods enable immich
ods disable immich
```

Your data is preserved when disabling. To re-enable later: `ods enable immich`

## Access

- **URL:** `http://localhost:2283`

## First-Time Setup

1. Enable the service: `ods enable immich`
2. Open `http://localhost:2283`
3. Create an admin account on first launch
4. Download the Immich app for iOS or Android and connect to your server

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `IMMICH_DB_PASSWORD` | PostgreSQL password (auto-generated) | _(required)_ |
