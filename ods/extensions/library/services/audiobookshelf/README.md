# Audiobookshelf for ODS

Audiobooks and podcasts with browser playback, listening progress, metadata,
RSS subscriptions and separate user accounts. Runs the actual upstream server
and web client, with its bundled FFmpeg for audio processing.

## Setup and media

Open `http://localhost:11047` and create the root account in the first-run setup.
Set a password before sharing access. No default credentials or example library
are supplied. Add an audiobook library pointing to `/audiobooks` and a podcast
library pointing to `/podcasts`; use the web upload controls for your audio files
and the podcast subscription controls for feeds you choose. These paths are
inside Docker, not Windows or macOS filesystem paths.

The recipe binds only loopback. Mobile devices and remote users require a
deliberately configured HTTPS reverse proxy, including WebSocket support; this
recipe does not expose your media to the LAN automatically. Metadata searches
and podcast downloads contact the upstream providers you choose in the app.

## Persistent storage

- `audiobookshelf-config:/config`: database, accounts, libraries and settings.
- `audiobookshelf-metadata:/metadata`: covers, logs, backups and cached artifacts.
- `audiobookshelf-audiobooks:/audiobooks`: uploaded audiobook files.
- `audiobookshelf-podcasts:/podcasts`: downloaded podcast episodes.

Keep the database on local Docker storage, not an SMB/NFS mount. Back up all
four volumes; metadata alone does not contain your entire library. Stop the
server for a consistent volume backup and preserve ownership UID/GID 1000 on
restore. Disabling the extension retains these volumes. A database upgrade can
require a backup to roll back; simply selecting an older image is insufficient.
Existing host libraries can be mounted only through explicit user configuration,
with appropriate permissions; no Portal project or whole host disk is mounted.

## Runtime and validation

Official GPL-3.0 release **2.36.1** pinned by digest, published for Linux amd64
and arm64. Windows/macOS require Docker's Linux engine; Linux uses the same
recipe. A small derived layer prepares writable directories before switching
to UID/GID 1000. Native `tini -- node index.js` remains intact. Port 8080 avoids
requiring a privileged bind. No GPU, model selection or context limit changes.

The service has a two-CPU, 2 GiB memory limit. Large scans and concurrent audio
transcoding may need deliberate resource adjustments. The Node health probe
requires HTTP 200 from the upstream `/healthcheck` endpoint with a timeout;
this proves server responsiveness, not account setup or successful playback.

Schema, Compose resolution and staging are checked separately. **Image build,
first-run setup, upload, playback, transcoding and restart/restore remain
runtime-unverified.** No model, app container or podcast download was started
while preparing this integration.

- [Versioned source](https://github.com/advplyr/audiobookshelf/tree/v2.36.1)
- [Docker installation](https://audiobookshelf.org/docs/documentation/install/docker/)
- [Release](https://github.com/advplyr/audiobookshelf/releases/tag/v2.36.1)
