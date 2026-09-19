# Shlink Extension for ODS

[Shlink](https://shlink.io/) is an open-source, self-hosted URL shortener with REST API, QR code generation, and visitor analytics.

## Quick Start

```bash
ods enable shlink
```

The service is available at `http://localhost:7856`. Compatible with Shlink web clients and third-party extensions.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `SHLINK_PORT` | Published host port | `7856` |
| `DEFAULT_DOMAIN` | Target short URL domain | `localhost:7856` |

## Persistence

SQLite database files and redirects are saved in `./data/shlink`.
Data is retained when restarting or updating the container.
