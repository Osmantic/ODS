# Compose Resolver Contracts

ODS assembles its runtime from a base compose file, hardware overlays,
mode overlays, and extension compose fragments. This document defines the
expected contract so maintainers and forks can add services or backends without
breaking unrelated install paths.

## Compose Layer Model

The resolved stack is built from these layers:

1. Base stack: common networks, volumes, and core services.
2. Hardware overlays: NVIDIA, AMD/Lemonade, Apple Silicon, Intel Arc, CPU, or
   other backend-specific runtime settings.
3. Mode overlays: local, cloud, hybrid, external Lemonade, or other routing
   modes.
4. Extension fragments: enabled service `compose.yaml` files and optional GPU
   overlays.
5. Operator overrides: local environment variables and supported flags.

The resolver, installer, CLI, dashboard, and validation tests should agree on
the same file set for a given mode and hardware class.

## Resolver Rules

- Do not require users to hand-compose files for supported install modes.
- Do not make a compose fragment valid only by accident of local state.
- A service referenced by `depends_on` must exist in every resolved file set
  that includes that dependency.
- Optional services should be gated by install/profile state, not by relying on
  missing files.
- Host ports must be declared through the port contract where applicable.
- Internal container ports should stay stable unless every dependent service and
  generated config writer is updated.
- Hardware overlays should change runtime flags, devices, images, or build
  targets only for the relevant backend.

## Adding A Service

Use the extension path unless the service is a core boot dependency.

Required files:

```text
extensions/services/<service-id>/manifest.yaml
extensions/services/<service-id>/compose.yaml
```

Recommended validation:

```bash
python scripts/audit-extensions.py --project-dir . <service-id>
python scripts/audit-extensions.py --project-dir .
docker compose -f docker-compose.base.yml -f extensions/services/<service-id>/compose.yaml config
```

If the service needs GPU-specific runtime flags, add backend overlays in the
service directory instead of modifying unrelated global overlays.

## Adding A Backend Or Mode

When adding a backend or mode:

1. Define the backend contract or mode behavior.
2. Add the compose overlay.
3. Update installer detection and compose selection.
4. Update generated config writers.
5. Update dashboard-api service/status assumptions if needed.
6. Add resolver validation for the exact file set.
7. Add support matrix and validation docs.

Backends commonly touch more than compose. Model selection, service URLs, health
checks, and dashboard diagnostics often need matching updates.

## Dependency Placeholders

Sometimes a mode uses an external or native process where another compose layer
expects a service name. If a placeholder is needed:

- document why it exists;
- avoid host port bindings when the placeholder never runs;
- keep health semantics honest;
- validate restart and reinstall behavior;
- prefer a ready-sidecar only when it checks a real external endpoint.

Placeholders should satisfy the compose dependency graph without claiming a
container is doing work that actually happens elsewhere.

## Port And Network Policy

- Default user-facing services should bind to localhost unless a LAN path is
  explicitly enabled.
- LAN and owner-card routes should use the documented proxy path.
- Internal service-to-service traffic should use container DNS and internal
  ports.
- Host-facing port defaults belong in `config/ports.json` and service manifests.
- Dashboard/API docs should match the actual auth and binding behavior.

Run the network exposure contract tests when changing host bindings or proxy
routes.

## Validation Commands

Use focused checks while developing:

```bash
python scripts/validate-golden-paths.py
python scripts/validate-generated-configs.py
python scripts/audit-extensions.py --project-dir .
```

Then validate representative compose sets for changed modes. For operational
changes, use the release-grade gate described in
[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md).

## Manual Compose Use

Manual compose commands are useful for debugging, but supported users should not
need to know the full file set. If a doc shows a manual compose command, include
all required base, mode, hardware, and extension files or point to the resolver.
