# Valkey

Authenticated RESP data service for explicit project caches, expiring values, lists, hashes, sorted sets and streams. This is the BSD-3-Clause Valkey server, with no web dashboard or model inference.

## Credentials and project connections

Set `VALKEY_PASSWORD` to 64 random hexadecimal characters. Startup validates its format and writes only its SHA-256 hash to a private temporary ACL file. Use username **ods**, the configured password and database **0**. The default unauthenticated user is disabled. Passwords are absent from server/healthcheck command arguments.

Host clients connect to `localhost:11091`; ODS containers use `valkey:6379`. Choose a key prefix such as `ods:my-project:` and use explicit expiry for cache entries. All keys and Pub/Sub channels must begin with `ods:`. Clients sharing this account can access each other's allowed keys: prefixes are organization, not per-project tenant isolation.

Administrative/dangerous commands, scripting, global key enumeration and databases other than 0 are unavailable. Libraries requiring Lua scripts, SCAN, CONFIG or unrestricted keys need a deliberately reviewed ACL adaptation; this is not a promise of compatibility with every Redis/Valkey queue library. Installing this extension does not change any application's database URL, add a client package or connect existing ODS services automatically.

## Memory and durability

The data-memory limit is 128 MiB with `noeviction`: when capacity is reached, writes that need more memory fail rather than silently deleting another project's data. Clients must handle these errors and set expiries appropriately. The container has a separate 512 MiB limit to leave room for overhead and persistence work, which is not a guarantee against all workloads exhausting memory.

`valkey-data` retains the append-only log under `/data`; fsync runs once per second. Recent writes can be lost after a crash. RDB periodic snapshots are disabled. For a backup, stop the service cleanly and copy the entire volume, including multipart AOF metadata/files, plus retain the password separately. Pub/Sub is ephemeral and is not a historical message store.

Runs as UID/GID 1000 with a read-only base, bounded temporary directory, one CPU and a 100-client limit. Host publishing is loopback-only. Connections are authenticated but not TLS encrypted; remote access requires separate TLS/network configuration. No operating-system tuning, GPU access or model changes are performed.

## Provenance and verification

Official Valkey 9.1.2 Alpine image is pinned by digest with amd64, arm64, ARM and ppc64le variants. Windows/macOS use a Linux-container Docker runtime, with named volumes rather than host-specific paths. Readiness uses authenticated native PING and requires the exact PONG response, not an HTTP request or a merely existing container definition.

Image build, ACL enforcement, client operations, memory exhaustion and persistence/restore/platform execution remain runtime-pending. Configuration checks do not prove those behaviors. No containers or inference were started.

Sources: [upstream release](https://github.com/valkey-io/valkey/tree/9.1.2), [ACL reference](https://valkey.io/topics/acl/), [official container](https://hub.docker.com/r/valkey/valkey).
