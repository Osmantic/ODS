# DokuWiki for ODS

A file-based wiki for documentation, page revision history and shared knowledge.
The GPL-2.0 application uses its native storage layout rather than a database
companion or a generated documentation placeholder.

## First setup

Install and enable `dokuwiki`. Open `http://localhost:11065/install.php` on the
first run, choose the wiki name and administrator credentials, enable ACLs and
select the access policy appropriate for your documents. No account or preset
password is installed by ODS. The normal launch action opens the wiki root after
setup. `DOKUWIKI_PORT` changes the loopback host port.

Finish installation before making the endpoint available to others. This recipe
only publishes local HTTP; it does not configure public access, TLS termination
or host firewall rules. Use DokuWiki's permissions to control anonymous reading,
editing and registration. No external mail service is supplied, so email password
recovery requires configuring an SMTP transport separately.

## Storage

`dokuwiki-storage` is mounted at `/storage`, owned by UID 1000. The official
entrypoint configures links for page/media data, local configuration and installed
plugins/templates while keeping bundled assets in the image. All changes made
through the wiki administration UI remain in the volume. Preserve this volume
when recreating the container; back it up with the application stopped.

PHP uploads are limited to 64 MiB and per-process memory to 256 MiB. Custom PHP
settings can be placed in `/storage/php.ini` using the upstream mechanism. No
plugins are downloaded automatically; plugin licenses and compatibility must be
checked separately. Farming, project source mounts, Portal chat imports and model
connections are not configured.

## Runtime and compatibility

Pinned official release 2026-07-14c for amd64/arm64, original Apache/PHP startup
under nonroot UID 1000 and a named volume. Docker Engine on Linux and Docker
Desktop Linux containers on Windows/macOS are the intended runtimes. There is no
GPU dependency or hardcoded language model.

The native `/health.php` loads DokuWiki initialization; it does not prove that the
owner completed installation or chose correct ACLs. Image build, setup/login,
editing, media uploads, permissions and restart/restore remain runtime-pending.
No application containers were started during this integration.

Application: https://github.com/dokuwiki/dokuwiki/tree/release-2026-07-14c
Official container: https://github.com/dokuwiki/docker
