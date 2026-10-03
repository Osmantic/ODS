# PdfDing for ODS

PDF library management with tags, collections, reading progress and annotations.
This complements PDF conversion tools: its purpose is organizing and reading
your stored documents. The active upstream is now on Codeberg; the old GitHub
repository is archived and points there.

## Initial setup

Set `PDFDING_SECRET_KEY` to a persistent random signing secret of at least 32
characters before enabling. After the server's migrations complete, create your
administrator interactively with the included Django management command:

```text
docker exec -it ods-pdfding python /home/nonroot/pdfding/manage.py createsuperuser
```

Follow the email/password prompts. Open `http://localhost:11058` and log in.
Public registration is closed, and no preset account or demo mode is enabled.
Create additional accounts deliberately through administration. No email service
is configured: password-reset email delivery needs explicit SMTP setup first.

Upload your PDFs through the application, organize them into collections, and
use the reader's highlights, comments and saved-position features. No local
document directory or Portal attachment is imported automatically. The consume
folder worker is disabled; enabling periodic ingestion later requires a specific
folder, permissions and its own configuration.

## Persistence and sharing

`pdfding-data:/data` retains both `/data/db` (SQLite) and `/data/media` (documents
and related files). Back up the entire volume while stopped for consistency,
retain UID/GID 1000 on restore and preserve the signing secret. Disabling must
not delete the volume. Keep a pre-upgrade backup before migrations.

Share links are an explicit in-app action; the recipe does not create any.
Only loopback port 11058 is published. Remote readers need an explicitly
configured HTTPS endpoint and matching host/origin settings. Secure-cookie
flags are disabled only for this local HTTP recipe; change them for HTTPS.
Supported local hostnames are `localhost`, `127.0.0.1` and Docker service
`pdfding`. No wildcard host acceptance, OIDC provider or embedded credentials.

## Runtime and evidence

AGPL-3.0 release **v1.14.0**, official Docker image pinned by digest for Linux
amd64/arm64. Windows/macOS use Docker's Linux engine. The derived image prepares
the unified data directory, then runs as the upstream UID/GID 1000. Original
migration/cleanup/Gunicorn startup is preserved, with Docker init, one worker
and three threads. Limits are one CPU and 1 GiB RAM; very large PDFs can need
more resources, including browser memory.

Python requests the application's actual `/healthz` endpoint with a timeout.
This checks availability, not rendered pages, authenticated access or correctly
saved annotations. No AI model, context size or GPU configuration is involved.

**Image build, account provisioning, upload, annotation, reading progress and
restart/restore remain runtime-unverified.** Schema/Compose/staging checks are
separate. No application container, document upload or model inference was run.

- [Active versioned source](https://codeberg.org/mrmn/PdfDing/src/tag/v1.14.0)
- [Versioned production configuration](https://codeberg.org/mrmn/PdfDing/src/tag/v1.14.0/pdfding/core/settings/prod.py)
- [Versioned SQLite deployment](https://codeberg.org/mrmn/PdfDing/src/tag/v1.14.0/compose/sqlite.docker-compose.yaml)
