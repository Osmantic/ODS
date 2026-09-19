# Vaultwarden Extension for ODS

[Vaultwarden](https://github.com/dani-garcia/vaultwarden) is an alternative Bitwarden compatible server written in Rust, optimized for self-hosted and low-resource environments.

## Quick Start

```bash
ods enable vaultwarden
```

Open `http://localhost:7840` in your browser. Compatible with standard Bitwarden browser extensions and mobile clients.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `VAULTWARDEN_PORT` | Published host port | `7840` |
| `SIGNUPS_ALLOWED` | Allow new user registrations | `false` |

## Persistence

All encrypted credentials, tokens, and SQLite database tables are stored in `./data/vaultwarden`.
Data is retained when stopping or updating the container.
