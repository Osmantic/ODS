# Gitea (Git Hosting)

Self-hosted lightweight Git server with code review, issue tracking, CI/CD, and wiki — a GitHub/GitLab alternative that runs on minimal resources.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
dream enable gitea
dream disable gitea
```

Your data is preserved when disabling. To re-enable later: `dream enable gitea`

## Access

- **URL:** `http://localhost:7830`
- **SSH:** `ssh://git@localhost:2222/<user>/<repo>.git`

## First-Time Setup

1. Enable the service: `dream enable gitea`
2. Open `http://localhost:7830`
3. Complete the initial setup wizard
4. Create your first repository

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `GITEA_HOST` | Hostname for Gitea server | `localhost` |
| `GITEA_PORT` | External port for web interface | `7830` |
| `GITEA_SSH_PORT` | External port for SSH access | `2222` |
| `GITEA_APP_NAME` | Display name for the instance | `Dream Server Git` |
