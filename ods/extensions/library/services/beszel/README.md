# Beszel Extension for ODS

[Beszel](https://github.com/henrygd/beszel) is a lightweight server monitoring hub with historical resource charts and container tracking.

## Quick Start

```bash
ods enable beszel
```

Open `http://localhost:7848` in your browser. Create your initial admin account on first launch.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `BESZEL_PORT` | Published host port | `7848` |

## Persistence

Metrics history and alert configurations are stored in `./data/beszel`.
Data is retained across service restarts and updates.
