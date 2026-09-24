# CrewAI

Multi-agent framework for AI workflows. Build teams of autonomous AI agents that collaborate, delegate tasks, and work together to solve complex problems through a no-code visual builder.

## Requirements

- **GPU:** CPU only — no GPU required
- **Dependencies:** None

## Enable / Disable

```bash
ods enable crewai
ods disable crewai
```

Your data is preserved when disabling. To re-enable later: `ods enable crewai`

## Preserve an existing Studio database before updating

The pinned Studio image uses `/CrewAI-Studio/crewai.db` for agents, tasks, crews
and tool configuration. Older ODS definitions mounted `/app` without directing
the database there, so container recreation discarded that database. The startup
command now links `crewai.db` to `/app/crewai.db` in the existing `data/crewai`
mount, while keeping Studio's application and image assets in their original
working directory.

Before recreating an old container, stop editing/running crews and snapshot its
SQLite database into the mounted directory. Keep Studio idle until recreation is
complete. Run on the Docker host (the ODS WSL terminal on Windows):

```bash
docker exec -i ods-crewai python3 - <<'PY'
from pathlib import Path
import sqlite3

source = Path('/CrewAI-Studio/crewai.db')
target = Path('/app/crewai.db')
if source.resolve() == target.resolve():
    raise SystemExit('Database already uses the data mount; no migration needed.')
if not source.is_file():
    raise SystemExit('No old database found; inspect this installation before updating.')
with target.open('xb'):
    pass
with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as old_db:
    with sqlite3.connect(target) as new_db:
        old_db.backup(new_db)
        assert new_db.execute('PRAGMA integrity_check').fetchone() == ('ok',)
print('Snapshot saved to /app/crewai.db. Keep Studio idle until recreation completes.')
PY
```

Back up the host `data/crewai` directory, then apply the updated definition and
recreate through ODS. Verify your saved agents, tasks and crews before resuming
writes. The snapshot refuses to overwrite an existing destination and includes
committed WAL data, but cannot include edits made after the snapshot. If the old
container has already been deleted, restore a prior backup or Studio JSON export.

Keep the database link startup command when rolling back unrelated changes.
Reverting it selects the ephemeral database path again. This fixes Studio's
SQLite state; files written by individual agent tools outside `/app` still need
their own explicit persistent mounts.

## Access

- **URL:** `http://localhost:8501`

## First-Time Setup

1. Enable the service: `ods enable crewai`
2. Open `http://localhost:8501`
3. Create agents, define tasks, and run crews through the visual builder
