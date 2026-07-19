# MinIO (S3 Object Storage)

S3-compatible object storage for Dream Server. Use it to store models, datasets,
backups, and any other large files that need S3 API access from other services.

## Usage

Enable from the dashboard or CLI:

```bash
dream enable minio
dream start minio
```

Requires `MINIO_ROOT_USER` and `MINIO_ROOT_PASSWORD` in `.env`.

## Access

| Interface | URL |
|---|---|
| S3 API | `http://127.0.0.1:${MINIO_PORT:-9002}` |
| Web Console | `http://127.0.0.1:${MINIO_CONSOLE_PORT:-9003}` |

From other Docker containers, use `http://minio:9000` (internal Docker DNS).

## Volumes

Data persists in `data/minio/` under the install directory.

## Ports

| Env Var | Default | Description |
|---|---|---|
| `MINIO_PORT` | 9002 | S3 API external port |
| `MINIO_CONSOLE_PORT` | 9003 | Web console external port |
