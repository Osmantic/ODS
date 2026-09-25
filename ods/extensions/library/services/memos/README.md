# Memos for ODS

Memos provides a persistent notebook for Markdown notes, research snippets, tags and attachments. It complements document search and the Portal chat: notes live in its own SQLite database and survive container replacement. This recipe does not automatically copy private chats or claim an agent connector is already configured.

## Install and first use

Install **Memos** from ODS Extensions, enable it, and open its application button. The default local address is `http://localhost:11018`; `MEMOS_PORT` changes the host port. Finish the initial owner setup in the application, then create a note and attach a small file. No provider key, GPU, cloud account or language model is required.

The recipe intentionally leaves the upstream instance URL unset. Do not publish this port to the Internet as part of initial setup. For an intentional remote deployment, configure the application's access policy, canonical URL and a TLS reverse proxy according to the upstream documentation.

## Storage and permissions

The Docker named volume `memos-data` stores `/var/opt/memos`, including SQLite and local attachments. Compose namespaces the volume under the ODS project. The service uses upstream UID/GID `10001:10001`; a newly created named volume inherits the image directory ownership. There are no Windows-, Linux- or macOS-specific host paths.

Stop Memos before backing up or restoring the entire data volume so that the database and attachments form one consistent snapshot. Disabling or replacing the container retains the volume. Removing volume data is destructive. When migrating an existing external database or older volume, check ownership and use the upstream migration procedure; this recipe does not recursively change the permissions of imported data.

## Platform and model behavior

The pinned official image offers Linux amd64, arm64 and ARM variants. Windows and macOS need a running Linux-container Docker environment; Apple Silicon uses arm64. This is a CPU application, not a native Metal or CUDA workload. It does not load, switch or change the context size of the ODS chat model.

## Readiness and validation

ODS checks the web listener at `/` and the container uses Alpine's `wget` against the same local endpoint. This confirms HTTP availability, not successful login or data persistence. The application has not been started for runtime validation in this change. After installation, verify owner setup, saving/reopening a note and attachment, and persistence after disabling/enabling the extension.

Code license: MIT. Deployment source: [official Docker guide](https://usememos.com/docs/deploy/docker). Image provenance and inspected architectures are recorded in `upstream.json`; the digest keeps installation reproducible even when upstream moves its `stable` tag.
