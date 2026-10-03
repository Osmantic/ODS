# Navidrome for ODS

Personal music streaming, album browsing, playlists and compatible Subsonic
clients. This is a music library server, separate from Audiobookshelf's podcast
and audiobook workflow. No sample music or external streaming subscription is
included.

## First use and music import

Open `http://localhost:11048` and create the administrator in the first-run UI.
Set a password before sharing access. Add individual listener accounts from the
administration UI. No fixed credentials or automatic Portal account mapping.

Navidrome scans files; its web player is not an upload manager. Copy your own
music into the persistent `/music` directory using Docker on the same computer:

```text
docker cp "./My Music/." ods-navidrome:/music/
```

Replace the quoted source with your actual directory. This command works from
PowerShell and POSIX shells with Docker installed. Files must be readable by
UID 1000; copied directories need traversal permission. The volume is writable
to support explicit imports, but the recipe does not mount the host music folder
or alter original host files. An existing library can instead be explicitly
configured as a read-only bind mount with suitable permissions.

The current `Scanner.Schedule` configuration triggers an hourly scan, and
`Scanner.ScanOnStartup` enables startup scans. After an import, use the admin
scan action to update the library immediately. File tags drive album and artist
organization; inaccurate tags can lead to unexpected grouping.

## Storage and access

`navidrome-data:/data` retains SQLite, accounts, playlists, configuration and
cache. `navidrome-music:/music` retains imported audio. Back up both while the
server is stopped for a consistent database copy; preserve UID/GID 1000 on
restore. Disabling the extension does not delete them. Keep a pre-upgrade backup
because schema upgrades may prevent a simple image downgrade.

Only loopback port 11048 is published. A phone or remote Subsonic client needs
an explicitly configured HTTPS endpoint; no LAN exposure is created here.
Public shares, external metadata services and the insights collector are
disabled. Enable any desired external integration deliberately in your own
configuration. No plugins, scrobbling accounts or remote credentials are seeded.

## Runtime and evidence

GPL-3.0 release **0.64.0**, official image pinned by digest. The published manifest
includes amd64, arm64 and additional Linux architectures; host support still
depends on the available Docker engine. Windows/macOS use Linux containers.
A derived layer owns the two volume directories for UID/GID 1000 and retains
the original `/app/navidrome` entrypoint. No root startup or GPU is required.

FFmpeg is included for CPU audio transcoding. The initial limit is two CPUs and
2 GiB RAM; large libraries or concurrent transcodes may require adjustment.
This integration does not configure jukebox playback on host audio devices,
change the loaded AI model or alter its context limit.

The bundled curl checks the actual `/ping` heartbeat and rejects HTTP errors
with a four-second timeout. It checks the listener, not library completeness or
successful audio playback. **Image build, first-user setup, import, scan,
transcoding, client login and restart/restore remain runtime-unverified.**
No app containers or model inference were started during preparation.

- [Versioned source](https://github.com/navidrome/navidrome/tree/v0.64.0)
- [Docker installation](https://www.navidrome.org/docs/installation/docker/)
- [Configuration](https://www.navidrome.org/docs/usage/configuration/options/)
