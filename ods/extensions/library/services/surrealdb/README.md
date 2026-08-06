# SurrealDB

Multi-model database with first-class graph, document, and relational querying
([SurrealQL](https://surrealdb.com/docs/surrealql)). Useful as a local knowledge-graph
or application database alongside ODS.

The GUI is **[Surrealist](https://surrealist.app/)** (desktop or web) — connect it to
this service; Surrealist is not shipped inside ODS.

## Requirements

- **GPU:** none
- **Dependencies:** none

## Enable / Disable

```bash
ods enable surrealdb
ods disable surrealdb
```

Data under `data/surrealdb/` is preserved when disabling.

## Access

| | |
|---|---|
| **HTTP endpoint** | `http://127.0.0.1:8900` |
| **User / password** | `SURREALDB_USER` / `SURREALDB_PASS` in `.env` (password auto-generated on first setup) |

Port defaults to **8900** so it does not collide with ChromaDB (8000) or llama-server.

### Connect Surrealist

1. Open [Surrealist](https://surrealist.app/) (or the desktop app)
2. New connection → endpoint `http://127.0.0.1:8900`
3. Auth: root credentials from `.env` (`SURREALDB_USER` / `SURREALDB_PASS`)
4. Create or select a namespace/database (e.g. `screech` / `main`)

### Health check

```bash
curl -sS http://127.0.0.1:8900/health
```

### SurrealQL (CLI via container)

```bash
docker exec -it ods-surrealdb /surreal sql \
  --endpoint http://127.0.0.1:8000 \
  --user "$SURREALDB_USER" --pass "$SURREALDB_PASS" \
  --ns main --db main
```

## Environment

| Variable | Description | Default |
|---|---|---|
| `SURREALDB_PORT` | Host port | `8900` |
| `SURREALDB_USER` | Root user | `root` |
| `SURREALDB_PASS` | Root password (required) | auto-generated |
