# Vikunja Extension for ODS

[Vikunja](https://vikunja.io/) is an open-source, self-hosted to-do and project management application featuring Kanban boards, Gantt charts, task lists, and calendars.

## Quick Start

```bash
ods enable vikunja
```

Open `http://localhost:7855` in your browser. Register your account to begin managing tasks and projects.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `VIKUNJA_PORT` | Published host port | `7855` |
| `VIKUNJA_SERVICE_JWTSECRET` | JWT token signature secret | _(change in production)_ |

## Persistence

SQLite database file (`vikunja.db`) and task file attachments are stored in `./data/vikunja`.
Data is preserved across upgrades and container recreation.
