# Docker Compose Service Architecture

## Current Architecture

All 16 services are defined as core in `docker-compose.base.yml` — there are no Docker Compose profiles. All services start together. To disable a service, comment it out in the compose file or use `docker-compose.override.yml` to override it.

### Starting Services

```bash
# NVIDIA
docker compose -f docker-compose.base.yml -f docker-compose.nvidia.yml up -d

# AMD
docker compose -f docker-compose.base.yml -f docker-compose.amd.yml up -d
```

### Disabling Individual Services

To skip a service, create `docker-compose.override.yml`:

```yaml
services:
  n8n:
    profiles: [disabled]    # Prevents this service from starting
  openclaw:
    profiles: [disabled]
```

### Checking What's Running

```bash
# See all services and their status
docker compose ps

# Check resource usage
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

## Historical Reference

ODS previously used Docker Compose profiles (`voice`, `workflows`, `rag`, `openclaw`, `monitoring`, `full`) to selectively start services. These were removed in favor of the current all-core architecture for simplicity. The installer automatically starts all services.

## See Also

- [EXTENSIONS.md](EXTENSIONS.md) — Adding new services
- [../QUICKSTART.md](../QUICKSTART.md) — Installation guide
- [../.env.example](../.env.example) — Configuration reference
