# MinIO — high-performance S3-compatible local object storage

An optional, secure S3-compatible object storage service with a built-in web management console. Use it for local AI workflows, document ingestion pipelines in RAG architectures, image output archiving from Stable Diffusion / ComfyUI, audio transcripts from Whisper, and local dataset / checkpoint caching without external cloud dependencies.

## Setup

Set root credentials in your ODS `.env` file before enabling **MinIO (S3 Object Storage)**:

- `MINIO_ROOT_USER`: administrative username / access key ID (defaults to `minioadmin`).
- `MINIO_ROOT_PASSWORD`: administrative secret key / password (minimum 8 characters).

If absent, running `setup.sh` automatically provisions a secure random 32-character root credential into `.env`. Compose refuses missing or empty passwords.

Once enabled:
- **S3 API Endpoint**: `http://localhost:9020` (published on port `9020` to prevent collisions with Whisper and native services on port `9000`).
- **Web Console UI**: `http://localhost:9021` (sign in with your root credentials).

Containers on the shared `ods-network` can access the S3 API directly at `http://minio:9000`.

## Settings

| Variable | Default | Description |
| --- | --- | --- |
| `MINIO_PORT` | `9020` | Published S3 API port on the host (internal listener remains `9000`) |
| `MINIO_CONSOLE_PORT` | `9021` | Published Web Console management UI port on the host (internal listener `9001`) |
| `MINIO_ROOT_USER` | `minioadmin` | Access key ID for MinIO administrative operations and API requests |
| `MINIO_ROOT_PASSWORD` | required | Secret access key for MinIO (minimum 8 characters) |
| `MINIO_BROWSER` | `on` | Toggle web console availability (`on` or `off`) |
| `MINIO_SERVER_URL` | `http://localhost:9020` | External endpoint URL advertised by the MinIO server |
| `MINIO_BROWSER_REDIRECT_URL` | `http://localhost:9021` | Redirect URL for web browser console access |

The default binding is loopback (`127.0.0.1`). Container privilege escalation is restricted via `no-new-privileges:true`.

## Python & AWS CLI Integration

### Python (`boto3`)

```python
import boto3
from botocore.client import Config

s3_client = boto3.client(
    "s3",
    endpoint_url="http://localhost:9020",
    aws_access_key_id="minioadmin",
    aws_secret_access_key="<YOUR_MINIO_ROOT_PASSWORD>",
    config=Config(signature_version="s3v4"),
    region_name="us-east-1",
)

# Create bucket and upload an AI document
s3_client.create_bucket(Bucket="rag-documents")
s3_client.upload_file("report.pdf", "rag-documents", "report.pdf")
```

### AWS CLI

```bash
aws --endpoint-url http://localhost:9020 s3 ls
```

## Data and Persistence

All object storage data and bucket metadata persist on the host under `./data/minio`. Disabling or restarting the container does not remove stored objects or buckets.

References: [MinIO Documentation](https://min.io/docs/minio/container/index.html), [MinIO Client (mc) Guide](https://min.io/docs/minio/linux/reference/minio-mc.html).
