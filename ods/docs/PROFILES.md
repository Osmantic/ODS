# Docker Compose service architecture

ODS uses `scripts/resolve-compose-stack.sh` as the authority for the active
Compose files and their order. The base file owns the small common control and
inference substrate. Each optional built-in owns its definition under
`extensions/services/<service-id>/compose.yaml`; a `.disabled` suffix keeps
that definition out of the graph.

Do not infer the active runtime from `config/core-service-ids.json`. That file
is the reserved built-in ID namespace used for collision protection.

## Resolve the active graph

```bash
flags=$(./scripts/resolve-compose-stack.sh \
  --script-dir "$PWD" \
  --tier 1 \
  --gpu-backend nvidia \
  --ods-mode local)

docker compose $flags config --services
docker compose $flags up -d
```

Normal installations persist the exact flags in `.compose-flags`; the ODS CLI
reuses that file for lifecycle operations.

## Installation profiles

- Full, Core, and Custom retain their existing feature-selection behavior.
- Assistant First is a fresh-install-only, opt-in public-beta profile on
  qualified Linux hosts: `./install.sh --assistant-first`.
- Assistant First uses an explicit allowlist and ignores user-extension and
  operator override fragments during first boot, preventing optional services
  from entering the graph accidentally.

See [Assistant First](ASSISTANT-FIRST.md) for its current candidate minimum and
qualification boundary, and [Extensions](EXTENSIONS.md) for service ownership
and lifecycle details.

## Inspect the running stack

```bash
ods status
docker compose ps
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```
