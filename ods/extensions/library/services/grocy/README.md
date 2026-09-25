# Grocy

Track food quantities and expiry, consume purchases, plan recipes and maintain shopping lists. This complements Homebox equipment inventory with grocery-specific workflows.

## Initial setup

Open http://localhost:11132 and use the native initial admin / admin login. Change that password immediately through user settings before connecting clients or exposing the service. ODS does not invent an environment variable to reset Grocy credentials. The host port is loopback-only; other containers on ods-network can still reach grocy:80. A responsive page does not prove that owner setup is complete.

Set GROCY_TZ to an IANA timezone if UTC is unsuitable. Configure products, units and locations in the native interface. No sample groceries, purchases, barcode-service credentials or external lookup provider are provisioned. A bundled demo barcode plugin is not an activated product-data integration.

## Persistence and startup

The grocy-config named volume retains /config, including data/grocy.db, data/config.php, uploads and application configuration. Back up the entire volume while stopped, not just the database. The upstream S6 /init starts as root to prepare directories and then runs application processes as abc (UID/GID 1000). Keep the native entrypoint and writable container filesystem; forcing a Compose user or read-only root breaks upstream initialization. No host directory or Docker socket is mounted.

The recipe allows two CPUs and 1 GiB memory. Its root-route HTTP probe checks availability and allows native database migration on that route; it does not verify grocery transactions or backups. Back up before upgrading and visit the root after an upgrade as upstream requires.

For a project consumer, create a native API key and configure that project's Grocy client explicitly. Installation does not automatically associate a Portal project, read pantry data, grant the model credentials or connect a scanner. The full chat setup workflow remains separate work.

## Provenance and validation

Grocy 4.7.1 is MIT licensed; the maintained LinuxServer image recipe is GPL-3.0-only. The pinned multiarchitecture digest identifies image v4.7.1-ls341. Do not use the archived grocy/grocy-docker image. Named volumes avoid host path assumptions on Linux and Windows/macOS Linux-container runtimes; both amd64 and arm64 manifests are available. GPU and loaded-model settings are not used or changed.

Build, native login/password change, stock transactions, API access, persistence/restore and each host platform remain unverified. No service or model was started.

Sources: [application](https://github.com/grocy/grocy/tree/v4.7.1), [container setup](https://github.com/linuxserver/docker-grocy/blob/70d77d7aa5e0f706afe9f19b133ae2a8629c8434/README.md).
