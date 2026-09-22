# Verdaccio

Private npm packages for ODS projects, with an authenticated public-package cache.
MIT upstream; official image pinned by digest, reporting version 6.10.4.

## Account and project setup

Provide a randomly generated 64-hex `VERDACCIO_PASSWORD` before installing.
Open `http://localhost:11115/`. The managed account is `ods`; public registration
is disabled. Startup hashes the supplied password using the pinned application's
bcrypt implementation (12 rounds) into a private temporary htpasswd file.
Rotate this account through the extension's configured secret and restart. Native
password changes are temporary and overwritten on restart; preserve your configured
secret. Existing issued tokens must also be considered when revoking credentials.

For one selected project, run login with an explicit project-local configuration:

```sh
npm login --auth-type=legacy --registry=http://localhost:11115 --userconfig=./.npmrc
```

Use username `ods` and the configured password. Keep `.npmrc` out of version control
because login writes a token. To route only private packages, add
`@ods:registry=http://localhost:11115/` to that project's `.npmrc`. Name private
packages `@ods/package-name` and explicitly publish with
`npm publish --registry=http://localhost:11115/`. For cache use, opt that project
into the registry explicitly; the recipe never changes global npm settings.

The `@ods/*` rule has no upstream proxy, preventing public fallback for the private
namespace. Other names are read-only proxies to npmjs.org and require authentication;
publishing there is disabled. No package, dependency install or project is preseeded.
Server-side npm audit forwarding and Gravatar are disabled. Public dependency cache
misses still contact npmjs.org; this is not an entirely offline registry.

## Persistence and operation

`verdaccio-storage` holds packages, cached metadata and the registry database/signing
state. Back it up with the service stopped and keep the configured secret separately.
Do not discard signing state during upgrades. There is no automatic cache eviction
or disk quota; monitor storage. Request bodies are limited to 50 MB.

The official nonroot UID 10001 runs with a read-only root and writable named storage.
Host port 11115 is loopback-only; containers on `ods-network` can reach port 4873,
but still need authentication. A remote deployment needs explicit TLS/network setup.
Project containers must use an appropriate reachable registry address, not their own
localhost. Project association through Portal remains separate work.

Linux amd64/arm64 images support Docker-based installation on compatible Windows,
Linux and macOS hosts without a GPU or model. `/-/ping` checks HTTP availability,
not authenticated publishing. Image build, login, publish/install and backup restore
remain pending runtime validation; no application/model was started for this recipe.

Sources: https://www.verdaccio.org/docs/docker/,
https://www.verdaccio.org/docs/authentication/ and
https://github.com/verdaccio/verdaccio/tree/6862cd700b1a9e210c13bcff9d4ababe444b935e.
