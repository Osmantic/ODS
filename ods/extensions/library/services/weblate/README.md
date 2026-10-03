# Weblate

Continuous localization for software and documentation: translation components, review, language files and version-control workflows. LibreTranslate supplies machine translation; Weblate manages human/project localization work.

## Setup and hardware

Provide distinct random 64-hex WEBLATE_DB_PASSWORD and WEBLATE_ADMIN_PASSWORD values and the actual WEBLATE_ADMIN_EMAIL. Open http://localhost:11138 and use the native admin account. Native startup reapplies the administrator name, email and password on every restart. Rotate that password in extension configuration as well as updating clients; changing only the UI password is undone on restart. PostgreSQL credentials are initialized once and must instead be rotated in the database before changing their configuration.

The recipe limits Weblate to two CPUs/3 GiB, PostgreSQL to 1 GiB and Valkey to 256 MiB, in addition to Docker overhead. One web process and one Celery worker reduce memory pressure. Larger translation repositories need deliberate tuning. Current x86 builds require x86-64-v2 features for NumPy. The wrapper checks every exposed CPU flags line before application initialization and reports a missing/hidden feature instead of blindly starting NumPy. Docker VM CPU features matter on Windows/macOS as well as Linux. ARM64 uses its own official image; no CUDA, GPU or model setting is involved.

## Projects and access

Registration is closed and login is required. Create a translation project/component with its real repository, branch, file pattern and source language. Choose read/push permissions explicitly and verify the diff before granting automatic push. No repository is cloned, no Git credentials or machine-translation provider is configured, and no remote commit/push is requested by installing this recipe. Portal project association is separate work.

SMTP is intentionally unconfigured. Notifications, invitations and password-reset mail need a real mail backend configured before use; the owner email is not proof of mail delivery. Native administration and explicit user creation remain available. No OAuth or external translation credentials are supplied automatically.

HTTP publishes only on loopback 11138. Other ods-network containers can reach weblate:8080. The canonical domain includes the selected host port; remote use requires configuring the real domain and HTTPS ingress. PostgreSQL and Valkey join only a private network and have no host ports.

## Data, upgrade and health

weblate-data retains /app/data, including repositories, uploads and the native signing secret. Preserve it together with weblate-db-data, the PostgreSQL database. weblate-cache retains filesystem caches; weblate-valkey-data retains queue/cache persistence. Back up the consistent stopped stack or follow native backup procedures for an online instance. A database-only copy omits Git working trees and signing material.

The official image runs as UID1000 with its native startup/migrations/supervisor and a read-only root. Temporary /run and /tmp are writable. Updating the Weblate image can migrate the schema; back up first and follow the supported upgrade path. Do not replace PostgreSQL major versions without an explicit database upgrade.

Health requires an HTTP-success response from /healthz/ and the upstream supervisor check. It does not prove repository synchronization, translation correctness, task completion or mail delivery.

## Provenance and validation

GPL-3.0-or-later Weblate 2026.9.1, official image release 2026.9.1.1 pinned by digest, with pinned PostgreSQL 17 and Valkey 9.1.2. Registry metadata includes amd64/arm64. Named volumes avoid host-path assumptions on Windows/Linux/macOS Linux-container runtimes; hardware compatibility is explicitly bounded above.

Build, first login/migrations, translation editing, repository synchronization, queues, mail and restore/platform execution remain unverified. No containers, repositories, browsers or models were started.

Sources: [Docker setup](https://docs.weblate.org/en/latest/admin/install/docker.html), [pinned startup](https://github.com/WeblateOrg/docker/tree/2763ab59b3e9f27585c3cc0e806b3424a818ea7e), [application source](https://github.com/WeblateOrg/weblate/tree/weblate-2026.9.1).
