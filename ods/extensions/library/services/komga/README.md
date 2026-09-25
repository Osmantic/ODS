# Komga for ODS

Comic, manga and ebook libraries with a browser reader, reading progress,
collections, user accounts and OPDS access. The actual Komga server processes
your documents; no example books, account shortcuts or remote sources are added.

## Initial setup and library import

Open `http://localhost:11049` and create your administrator using the initial
account screen. Configure reader accounts and their library permissions from
the administration UI before allowing other people access.

Import your own files into the persistent books volume, for example:

```text
docker cp "./My Books/." ods-komga:/data/
```

Replace the quoted source with an existing local directory. This uses the Docker
CLI on Windows, Linux or macOS; it does not copy anything until you run it.
Files and directories must be readable/traversable by UID 1000. In Komga, create
a library pointing to `/data` (or a subdirectory) and scan it. Arrange series in
separate folders so the library follows the intended grouping. Use supported
CBZ/CBR, PDF or EPUB documents; not every format supports identical reader or
metadata features. Large archives take time to analyze and create thumbnails.

An existing host collection may instead be mounted explicitly with suitable
permissions. The default recipe mounts only its own volumes, not Portal
projects, the host desktop or other extensions' libraries.

## Persistence and recovery

`komga-config:/config` stores the databases, configuration, search indices,
fonts and logs. `komga-books:/data` stores the imported documents. Preserve both
on disable and back up both for recovery: a configuration backup is not a copy
of your books. Stop the server before copying SQLite databases and retain
UID/GID 1000 on restoration. Keep a pre-upgrade backup because migrations can
make direct image downgrades unsuitable.

The local port binds loopback only. Remote readers, OPDS clients or reader-device
integrations need an explicit reachable endpoint and suitable HTTPS setup. No
OAuth credentials, remote identity provider or device account is preconfigured.

## Runtime and validation

MIT release **1.27.0**, official image pinned by digest, published for Linux
amd64, arm64 and ARM. Docker on Windows/macOS runs the Linux image. Upstream
uses architecture-specific Java/native libraries, so identical feature support
on every architecture is not asserted by this recipe.

A derived layer owns both directories for UID/GID 1000 and retains the upstream
Java entrypoint and Docker profile. The container has two CPUs and 2 GiB RAM;
the JVM heap is capped at 1536 MiB, leaving room for native allocations. Adjust
both deliberately if a large library needs more memory. No GPU or AI model
configuration is involved.

The bundled curl checks `/actuator/health` and rejects HTTP errors. Only the
health actuator is exposed, without diagnostic details. Startup gets a
120-second health grace period for initial migrations. This does not verify
successful import, rendered pages, login or restored reading progress.

**Build, account setup, library import, browser reading and restart/restore are
runtime-unverified.** Schema/Compose/staging checks do not replace these tests.
No application container or model was started during preparation.

- [Versioned source](https://github.com/gotson/komga/tree/1.27.0)
- [Docker installation](https://komga.org/docs/installation/docker/)
- [Documentation](https://komga.org/docs/)
