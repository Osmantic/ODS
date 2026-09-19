# Wallabag Extension for ODS

[Wallabag](https://wallabag.org/) is a self-hosted read-it-later web application that extracts article text and images for comfortable, distraction-free reading.

## Quick Start

```bash
ods enable wallabag
```

Open `http://localhost:7853` in your browser. Default login username is `wallabag` with password `wallabag`. Change this password on first access.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `WALLABAG_PORT` | Published host port | `7853` |
| `SYMFONY__ENV__DOMAIN_NAME` | Base public URL | `http://localhost:7853` |

## Persistence

SQLite database and configurations reside in `./data/wallabag/data`.
Cached article images and assets are stored in `./data/wallabag/images`.
Data is preserved across upgrades and container recreation.
