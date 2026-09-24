# Langflow

Visual LLM workflow builder with drag-and-drop interface. Create complex AI workflows, RAG pipelines, and AI agents using LangChain components with real-time testing.

## Requirements

- **GPU:** NVIDIA, AMD, or Apple Silicon
- **Dependencies:** None

## Enable / Disable

```bash
ods enable langflow
ods disable langflow
```

Your data is preserved when disabling. To re-enable later: `ods enable langflow`

## Database persistence and existing installations

Langflow 1.8.0 defaults its SQLite database to the Python package directory,
outside ODS's data mount. ODS sets `LANGFLOW_SAVE_DB_IN_CONFIG_DIR=true` so new
installations store it in `/app/data/.cache/langflow/langflow.db`, alongside
Langflow's existing configuration under `data/langflow` on the host.

**Before updating or recreating an existing container that uses the old default,**
stop editing/running flows and take the following consistent SQLite snapshot.
Run this from a terminal on the Docker host while the old container still exists
and is running. On Windows, use the ODS WSL terminal.

```bash
docker exec -i ods-langflow python - <<'PY'
from pathlib import Path
import sqlite3
from langflow.services.deps import get_settings_service

settings = get_settings_service().settings
url = settings.database_url
if not url.startswith("sqlite:///"):
    raise SystemExit("External database configured: follow its backup procedure instead.")
source = Path(url.removeprefix("sqlite:///"))
target = Path(settings.config_dir) / "langflow.db"
if source.resolve() == target.resolve():
    raise SystemExit("Database already uses the persistent config directory; no migration needed.")
if not source.is_file():
    raise SystemExit(f"Database not found: {source}; inspect this installation before updating.")
target.parent.mkdir(parents=True, exist_ok=True)
# Exclusive creation prevents overwriting a previous backup or live database.
with target.open("xb"):
    pass
with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old_db:
    with sqlite3.connect(target) as new_db:
        old_db.backup(new_db)
        assert new_db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
print(f"Snapshot saved to {target}. Keep flows idle until container recreation completes.")
PY
```

Back up the complete host `data/langflow` directory, including hidden files and
the existing secret key. Then apply the updated Compose configuration and
recreate Langflow through the normal ODS update/enable flow. Verify your flows
and credentials before resuming writes. A snapshot does not include changes
made after it was taken. If the old container has already been removed, its
ephemeral database cannot be recovered from the data volume; restore an earlier
database backup or exported flows.

Keep `LANGFLOW_SAVE_DB_IN_CONFIG_DIR=true` if rolling back other Compose changes.
Removing it selects the old ephemeral path again. An operator-provided
`LANGFLOW_DATABASE_URL` in a Compose override continues to take precedence;
this change does not migrate an external database.

## Access

- **URL:** `http://localhost:7802`

## First-Time Setup

1. Enable the service: `ods enable langflow`
2. Open `http://localhost:7802`
3. Create a new flow from the template gallery or start from scratch
4. Drag LLM, retriever, and tool nodes onto the canvas

### API Usage

```bash
# Run a flow
curl -X POST http://localhost:7802/api/v1/run/<flow_id> \
  -H "Content-Type: application/json" \
  -d '{"input_value": "Hello, what can you help me with?"}'
```
