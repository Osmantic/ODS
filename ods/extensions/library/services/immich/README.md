# Immich

High-performance self-hosted photo and video backup solution with AI-powered organization. Features automatic backups from mobile devices, face recognition, object detection, and automatic tagging.

## Requirements

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
