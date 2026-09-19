# Calibre-Web Extension for ODS

[Calibre-Web](https://github.com/janeczku/calibre-web) is a clean web interface for browsing, reading, and downloading eBooks using a Calibre database.

## Quick Start

```bash
ods enable calibre-web
```

Open `http://localhost:7852` in your browser. Default login is `admin` with password `admin123`.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `CALIBRE_WEB_PORT` | Published host port | `7852` |

## Persistence

Settings, user reading progress, and metadata are saved to `./data/calibre-web/config`.
Books and the Calibre `metadata.db` are stored in `./data/calibre-web/books`.
Data is preserved across upgrades and container recreation.
