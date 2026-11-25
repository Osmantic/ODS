# Docker Compose Service Selection

ODS combines its base stack, platform overlays and selected extension fragments.
Use the ODS lifecycle commands to preserve that selection when starting or
restarting services. A hand-written `docker compose -f ...` command uses only the
files you supply and may omit selected extensions or generated configuration.

## Enabling and Disabling Extensions

Use the **Extensions** page to enable or disable an optional extension. An
enabled extension has a `compose.yaml`; disabling it changes that file to
`compose.yaml.disabled`. The resolver omits disabled fragments. Dependencies can
prevent disabling an extension that another selected extension needs.

On systems using the common `ods-cli`, the corresponding commands are:

```bash
ods disable n8n
ods enable n8n
```

On macOS, use the Extensions page for selection. The `ods-macos.sh` command
supports `start`, `stop` and `restart`, but does not implement `enable` or
`disable`. Stopping a service alone does not remove it from the selected stack.

## Custom Compose Overrides

An owner can add `docker-compose.override.yml` in the installation directory.
ODS resolves and scans a newly added override before including it in subsequent
start or restart commands, even when the saved `.compose-flags` predates it.

Overrides must pass the resolver's extension-content checks. In particular,
they cannot redeclare protected built-in service names such as `n8n`,
`dashboard-api` or `llama-server`. The former example that put
`profiles: [disabled]` on `n8n` or `perplexica` in this file is rejected by those
checks; use extension selection instead. A rejected override is omitted and the
command prints a warning explaining why.

## Checking What's Running

```bash
ods status
docker stats --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
```

## See Also

- [EXTENSIONS.md](EXTENSIONS.md) - Extension selection and recipe requirements
- [../QUICKSTART.md](../QUICKSTART.md) - Installation guide
- [../.env.example](../.env.example) - Configuration reference
