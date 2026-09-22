# Homepage for ODS

Homepage is an optional service dashboard for project links and upstream-supported
API widgets. It does not replace the ODS dashboard, install applications, or claim
that linked services are running. The initial configuration contains only the
Portal link; it does not seed upstream's example services or weather API keys.

## Runtime and setup

- Official Homepage **2.4.0**, GPL-3.0, pinned by OCI digest. Both linux/amd64 and
  linux/arm64 are published. Windows/macOS require Docker's Linux engine; native
  Linux uses the same recipe. No GPU, model, inference server or context change.
- The small build layer prepares `/app/config` for UID/GID 1000. The official
  entrypoint runs as that user, with `no-new-privileges`; no host paths, Docker
  socket, external discovery service or privileged container are mounted.
- Configure `HOMEPAGE_AUTH_PASSWORD` and a **random `HOMEPAGE_AUTH_SECRET` of at
  least 32 characters** before enabling. Keep the secret stable across restarts.
  Upstream rejects a shorter secret. These are separate from Portal credentials.
- Open `http://localhost:11038` and sign in with the configured password.
  `HOMEPAGE_EXTERNAL_URL` must match the browser's origin. If the published port
  changes, also change the URL and `HOMEPAGE_ALLOWED_HOSTS`. Host entries include
  ports but no schemes; keep `homepage:3000` for ODS's internal readiness probe.
  Do not replace this list with `*`. The published port binds only loopback.
- `HOMEPAGE_VAR_ODS_URL` sets the Portal link, default `http://localhost:3001`.
  It is a browser destination, not a container API address.

The versioned LICENSE and image metadata say GPL-3.0. The source Dockerfile still
has an Apache-2.0 label; that stale label is not the license used by this recipe.

## Persistent configuration and real services

The `homepage-config` named volume retains YAML, custom CSS/JS and logs. A fresh
volume receives `config/` from the image. Existing configurations survive image
updates; upgrading this recipe never overwrites the owner's service list.
To edit it without an OS-specific bind mount, copy the files through Docker:

```text
docker cp ods-homepage:/app/config ./homepage-config
```

Edit `homepage-config/services.yaml`, then copy only the file you changed:

```text
docker cp ./homepage-config/services.yaml ods-homepage:/app/config/services.yaml
```

Use the application's refresh control after editing. Preserve UID/GID 1000 when
restoring backups so configuration and logs remain writable. Back up the named
volume along with the separately stored auth secret; do not delete it on disable.

For a service you actually enabled in ODS, add its browser URL as `href`. API
widgets instead need the service's internal Docker hostname, port and explicitly
configured credentials. Select a supported widget from the upstream reference;
do not assume a browser link alone provides API monitoring. Never put API secrets
in `href` or descriptions. No widgets are preauthorized to read other ODS apps,
and no services are automatically installed by adding a link here.

The native `/api/healthcheck` answers without authentication and exposes only
listener status. It does not prove successful sign-in, valid YAML, widget access
or reachability of linked applications. Limits are one CPU and 1 GiB RAM.
Remote publishing needs an intentional authenticated HTTPS deployment; this
recipe configures local use only.

## Verification status

Image provenance/platforms, versioned startup/auth/health source, schema,
Compose resolution and ODS staging are checked separately from application use.
**Image build and application runtime remain pending.** For local acceptance,
verify password sign-in, edit a real service link, refresh, restart, and confirm
the edit and password still work. No containers or model inference were launched
to prepare this recipe.

## Upstream evidence

- [Versioned project](https://github.com/gethomepage/homepage/tree/v2.4.0)
- [Versioned license](https://github.com/gethomepage/homepage/blob/v2.4.0/LICENSE)
- [Installation and host validation](https://gethomepage.dev/installation/)
- [Service configuration](https://gethomepage.dev/configs/services/)
- [Widget reference](https://gethomepage.dev/widgets/)
