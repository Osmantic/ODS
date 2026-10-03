# ODS web gateway

This optional Caddy proxy makes the ODS web surfaces available on port 80 with
host-based routing. Without it, Dashboard, chat, and API bind to `127.0.0.1` by
default, so `http://<device>.local` does not reach them.

| Hostname | Destination |
| --- | --- |
| `<device>.local` | Redirect to chat |
| `chat.<device>.local` | Open WebUI |
| `dashboard.<device>.local` | ODS Dashboard |
| `auth.<device>.local` | Dashboard API magic-link redemption |
| `api.<device>.local` | Dashboard API |
| `hermes.<device>.local` | Hermes proxy, when enabled |
| `talk.<device>.local` | Dashboard Talk |

Each backend remains mounted at its own root path. The `ods-session` cookie can
be scoped to `<device>.local` so sign-in works across these subdomains.
