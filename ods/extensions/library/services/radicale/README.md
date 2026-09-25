# Radicale — private calendars and contacts

Radicale 3.8.0 (GPL-3.0) provides CalDAV/CardDAV storage for calendar, task and contact clients. Its built-in web interface manages collections; it is not a full calendar editor. Upstream: https://github.com/Kozea/Radicale and https://radicale.org/v3.html.

## Configuration and use

Set required secret `RADICALE_PASSWORD` to 64 random hexadecimal characters through the ODS extension configuration. The account is `ods`. Startup hashes the secret with bcrypt into a private temporary htpasswd file; no default password, anonymous access or external authentication provider is enabled. Changing the configured secret and recreating the container changes the password without deleting collections; update saved client credentials accordingly.

Open `http://localhost:11097/.web/` to create a calendar/address book, then explicitly configure your DAV client with `http://localhost:11097/` and those credentials. No existing calendars, contacts, accounts or devices are imported or connected automatically. Owner-only rights constrain collections to their account. This single-owner recipe does not provision additional users or enable sharing.

The host port binds to loopback only. A client on another computer cannot reach it without deliberately configured routing and HTTPS termination. Do not send Basic credentials across an unencrypted remote connection. Inside the ODS Docker network the service is `radicale:5232`; credentials are still required.

## Persistence and lifecycle

The named `radicale-data` volume stores `/data/collections`, including native collection metadata and locks. Back up the entire volume while stopped for a consistent copy, retain the configured secret separately, and restore ownership to UID/GID 1000. Removing the extension must not implicitly erase the data volume. No source directory or host calendar folder is mounted.

Nonroot, read-only container; private temporary authentication files are regenerated after restart. Limit: 512 MiB/one CPU, 20 simultaneous connections, 100 MB requests and 10 MB individual resources. The health probe checks only the built-in web page; it does not prove successful authenticated synchronization or create test collections.

## Platforms and verification

Pinned Python base offers Linux amd64/arm64 images for Docker Engine or Docker Desktop on Windows/Linux/macOS. Python dependencies are version-pinned and installation requires binary wheels, so unsupported architectures fail rather than silently compiling an unverified backend. No GPU/model is required or selected.

Recipe/schema validation is separate from application verification. Image build, actual DAV authentication, client compatibility, restart/restore and host-platform runtime validation remain pending. No app or model was started while preparing this recipe.
