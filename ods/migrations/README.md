# Configuration Migrations

This directory contains configuration migration scripts for ODS.

## Purpose

When ODS is updated between versions, configuration files (`.env`, `docker-compose.yml`) may need changes. Migration scripts ensure user configs are updated safely and automatically.

## How It Works

1. **Version Tracking**: The system tracks which migrations have run using `.migration-state` file
2. **Automatic Detection**: `migrate-config.sh check` compares current version vs last migrated
3. **Safe Migration**: Each migration creates a backup before making changes
4. **Incremental**: Migrations run in order (v0.1.0 → v0.2.0 → v0.3.0)

## Usage

```bash
# Check if migration needed
cd ods
./scripts/migrate-config.sh check

# Show what changed
./scripts/migrate-config.sh diff

# Run migrations (with automatic backup)
./scripts/migrate-config.sh migrate

# Manual backup
./scripts/migrate-config.sh backup
```

## Creating New Migrations

1. Create a new file: `migrations/migrate-vX.Y.Z.sh`
2. Make it executable: `chmod +x migrations/migrate-vX.Y.Z.sh`
3. Follow this template:

```bash
#!/bin/bash
# Migration: vA.B.C → vX.Y.Z
# Description: What this migration does
# Date: YYYY-MM-DD

set -e

echo "Migrating configuration to vX.Y.Z..."

# Your migration logic here
# - Add new env vars
# - Rename old vars
# - Update file formats
# etc.

echo "Migration vX.Y.Z complete"
```

## Migration Guidelines

### DO:
- Always use `set -e` to fail on errors
- Check if changes are needed before making them
- Add comments explaining what changed
- Test migrations on a clean environment

### DON'T:
- Delete user data
- Change working configurations without backup
- Assume files exist (check first)
- Make breaking changes without warning

## Integration with Updates

The `ods-update.sh` script automatically runs migrations during updates:

```bash
# In ods-update.sh
cd ods
./scripts/migrate-config.sh migrate || {
    echo "Config migration failed"
    exit 1
}
```

## Troubleshooting

**"Migration already applied"**
- Check `.migration-state` file in data directory
- Delete it to re-run migrations (use with caution)

**"Migration failed"**
- Check the backup in `~/.ods/backups/`
- Restore manually if needed
- Review migration script for errors

**Missing environment variables after update**
- Run `./scripts/migrate-config.sh diff` to see what's new
- Run `./scripts/migrate-config.sh migrate` to add them

## Examples

### Adding a new environment variable

```bash
#!/bin/bash
ENV_FILE="${INSTALL_DIR}/.env"

if [[ -f "$ENV_FILE" ]]; then
    if ! grep -q "^NEW_VAR=" "$ENV_FILE"; then
        echo "" >> "$ENV_FILE"
        echo "# New feature configuration (v0.3.0+)" >> "$ENV_FILE"
        echo "NEW_VAR=default_value" >> "$ENV_FILE"
    fi
fi
```

### Renaming an environment variable

```bash
#!/bin/bash
ENV_FILE="${INSTALL_DIR}/.env"

if [[ -f "$ENV_FILE" ]]; then
    # Check if old var exists
    if grep -q "^OLD_VAR=" "$ENV_FILE"; then
        # Get old value
        OLD_VALUE=$(grep "^OLD_VAR=" "$ENV_FILE" | cut -d= -f2)
        # Add new var
        echo "NEW_VAR=$OLD_VALUE" >> "$ENV_FILE"
        # Comment out old var
        sed -i 's/^OLD_VAR=/# OLD_VAR= (renamed to NEW_VAR in v0.3.0)/' "$ENV_FILE"
    fi
fi
```

## See Also

- `../scripts/migrate-config.sh` — Migration manager
- `../scripts/ods-update.sh` — Update system that calls migrations
- `../../docs/SHIP-PUNCH-LIST.md` — Item #14 (config migration story)
