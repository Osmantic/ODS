# Extension Templates

These files are starter patterns for downstream builders and service authors.
They are templates only; they are not loaded by the service registry until you
copy them into `extensions/services/<your-service>/` and rename the manifest to
`manifest.yaml`.

## Files

| Template | Use it when |
|----------|-------------|
| `service-template.yaml` | You need a commented `manifest.yaml` starting point |
| `compose-template.yaml` | Your service has one normal Docker Compose definition |
| `compose-gpu-swap.yaml` | Your service has a CPU base image and GPU-specific image tags |
| `compose-gpu-only.yaml` | Your service only runs with a GPU and needs backend-specific compose files |
| `dashboard-plugin-template.js` | Your service needs a dashboard plugin entry point |

## Copy Path

```bash
mkdir -p extensions/services/my-service
cp extensions/templates/service-template.yaml extensions/services/my-service/manifest.yaml
cp extensions/templates/compose-template.yaml extensions/services/my-service/compose.yaml
```

Then edit:

- `service.id`
- `service.name`
- `service.container_name`
- `service.default_host`
- `service.port`
- `service.external_port_env`
- `service.external_port_default`
- `service.health`
- `service.depends_on`
- `service.env_vars`
- feature IDs and required services
- compose service name, image, ports, volumes, and healthcheck

The manifest `service.id`, compose service key, `container_name`, and data
directory should all use the same service identity.

## Validation

From the `dream-server/` directory:

```bash
python scripts/audit-extensions.py --project-dir . my-service
python scripts/audit-extensions.py --project-dir .
docker compose -f docker-compose.base.yml -f extensions/services/my-service/compose.yaml config
git diff --check
```

If your service has GPU overlays, run compose config with the relevant platform
overlay as well:

```bash
docker compose -f docker-compose.base.yml -f docker-compose.nvidia.yml \
  -f extensions/services/my-service/compose.yaml \
  -f extensions/services/my-service/compose.nvidia.yaml config
```

## Compatibility Notes

Extensions are most stable when they:

- keep persistent data under `./data/<service-id>/`
- expose only the ports users need
- declare secrets in `service.env_vars`
- use healthchecks that work before login
- avoid broad host filesystem mounts
- avoid changing core service IDs or aliases

For broader downstream guidance, read
[`docs/BUILD-ON-DREAM-SERVER.md`](../../docs/BUILD-ON-DREAM-SERVER.md).
