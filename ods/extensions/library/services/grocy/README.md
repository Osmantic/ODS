# Grocy Extension for ODS

[Grocy](https://grocy.info/) is a self-hosted web-based ERP application for your household pantry, inventory, chores, and meal management.

## Quick Start

```bash
ods enable grocy
```

Open `http://localhost:7851` in your browser. Default login username is `admin` with password `admin`. Change this password upon first sign-in.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `GROCY_PORT` | Published host port | `7851` |

## Persistence

SQLite databases, barcode caches, and household configurations are stored in `./data/grocy`.
Data is retained across container rebuilds and version updates.
