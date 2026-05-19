# Paperless-ngx

Document management system that transforms physical documents into a searchable online archive. Automatic OCR, tagging, classification, and full-text search for PDFs and images.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
dream enable paperless-ngx
dream disable paperless-ngx
```

Your data is preserved when disabling. To re-enable later: `dream enable paperless-ngx`

## Access

- **URL:** `http://localhost:7807`

## First-Time Setup

1. Enable the service: `dream enable paperless-ngx`
2. Open `http://localhost:7807`
3. Create an admin account on first launch
4. Upload your first document via the web interface or email import

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `PAPERLESS_SECRET_KEY` | Django secret key for session security (auto-generated) | _(required)_ |
