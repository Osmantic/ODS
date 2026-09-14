# Audiobookshelf

Browse local audiobooks, recorded research and generated speech, stream audio to
authenticated clients and retain listening progress. This optional recipe pins
upstream **2.36.0** and requires no GPU.

## Initial setup and media

Keep ODS `BIND_ADDRESS=127.0.0.1` during initial setup. Install from Extensions,
open `http://localhost:7833`, and create the first administrator with a strong,
unique password. An empty instance has no owner yet; complete this locally before
configuring remote access. Health reports service availability, not completion
of account setup.

Place media on the host under `data/audiobookshelf/audiobooks`, organized into
one directory per book or recording. For example:

```text
data/audiobookshelf/audiobooks/Local Research/Weekly Report/report.mp3
```

In the web interface, add an audiobook library using `/audiobooks` and scan it.
The source mount is read-only: the server can read and stream recordings while
library metadata, covers, caches and listening progress are stored separately.
Upload, delete, tag-editing or other operations that change source files require
an explicit operator change to that mount. Podcast downloads need a separate
writable media directory; no podcast feed or download is configured by this recipe.

Local files can come from your existing media collection or ODS speech workflows.
Online metadata matching, podcast feeds and public sharing are separate features
that operators choose explicitly. Review permissions when adding listener accounts.
For remote use, configure a protected HTTPS proxy or encrypted tunnel; the default
host port is `AUDIOBOOKSHELF_PORT=7833` on loopback.

## Persistent state and maintenance

The image runs as UID/GID 1000; ODS prepares bind-mount ownership. `config/` holds
the database and authentication state. `metadata/` holds covers, caches and other
application data. Preserve both directories and the original media files. The
container root is read-only with temporary scratch limited to 128 MiB, 2 CPU and
1 GiB memory. Large libraries or concurrent transcoding need capacity assessment.

Before upgrading, stop the service and back up the whole `data/audiobookshelf`
directory. Read upstream migration notes. Restoring an older image may require
its matching database backup; never merge a backup into a running database.
Disabling the extension preserves files. Keep the database on a local filesystem
and do not file-sync it while the server is running.

The regression workflow uses actual media scanning, authenticated playback and
saved progress across container recreation. Browser/mobile playback, podcast
downloads, online metadata, native ARM/macOS/Windows, existing user database
upgrades and large/concurrent workloads require separate validation.

Upstream: [Docker documentation](https://audiobookshelf.org/docs/documentation/install/docker/),
[pinned source](https://github.com/advplyr/audiobookshelf/tree/v2.36.0).
