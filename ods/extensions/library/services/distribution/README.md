# CNCF Distribution

Authenticated local OCI/container-image registry. Verdaccio stores npm packages; this service stores container manifests and layers produced by project builds.

## Credentials and clients

Supply distinct random 64-hex DISTRIBUTION_PASSWORD and DISTRIBUTION_HTTP_SECRET values. The account is ods. Startup hashes the password with bcrypt through stdin, creates a private temporary authentication file and preserves the native registry entrypoint. Restarting reapplies the configured password, so rotate it in the extension configuration and update clients together. Keep the HTTP secret stable to retain upload-state validity. There is one shared account; image-name prefixes are not access-control boundaries.

This recipe publishes HTTP only on 127.0.0.1:11136 for local development. It is not configured as an Internet registry. Docker clients generally treat localhost as a local insecure registry; confirm the behavior of your actual Docker/Podman/BuildKit engine before pushing. Remote engines resolve localhost on their own host, not on the ODS desktop. ODS does not rewrite daemon trust settings or disable TLS checks. Configure a real hostname, trusted TLS and appropriate authentication/authorization before remote use.

Use docker login localhost:11136 --username ods and enter the password interactively. Tag an existing project image with the localhost:11136/project-name/image-name namespace, then push that exact tag. Pulling uses the same registry name. No image is built, copied, pushed or executed by installing this extension. Project association and client credentials are explicit setup steps.

Containers on ods-network can reach distribution:5000, but that address is not automatically resolvable by a host Docker daemon. Never assume a browser URL or container DNS name is a valid registry address for every build engine.

## Storage and operation

The distribution-data named volume retains manifests, blobs and upload state in /var/lib/registry. Back it up while stopped, together with the extension configuration and stable secrets. UID/GID 1000, a read-only root, private temporary authentication files, two CPUs and 1 GiB memory bound the service. Image storage capacity still depends on the Docker host.

Deletion is disabled. Garbage collection is an explicit maintenance operation that must follow upstream read-only/offline guidance; the recipe runs no automatic pruning. No Docker socket, host source directory, upstream mirror or remote object-storage credentials are mounted. OpenTelemetry trace export is disabled.

The health probe authenticates to the native /v2/ API and the registry checks its storage driver. This does not validate layer upload/pull correctness. There is no browser dashboard, so the catalog has no fictional web launch.

## Provenance and validation

Apache-2.0 Distribution 3.1.1, official registry image pinned by digest. amd64/arm64 manifest availability verified. Named volumes avoid OS-specific paths with Linux Docker and Windows/macOS Linux-container runtimes; client network/trust setup remains host-specific. Build, authentication, push/pull, interrupted uploads, restore and each host platform remain unverified. No services, models or image operations were started.

Sources: [upstream](https://github.com/distribution/distribution/tree/v3.1.1), [deployment and TLS](https://distribution.github.io/distribution/about/deploying/), [configuration](https://distribution.github.io/distribution/about/configuration/).
