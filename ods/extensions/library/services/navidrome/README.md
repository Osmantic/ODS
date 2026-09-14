# Navidrome Extension for ODS

[Navidrome](https://www.navidrome.org/) is an open-source, web-based music server and streamer compatible with Subsonic and Airsonic clients.

## Quick Start

```bash
ods enable navidrome
```

Open `http://localhost:7849` in your browser. Place your audio files into `./data/navidrome/music`.

## Configuration

| Variable | Description | Default |
|----------|------------|---------|
| `NAVIDROME_PORT` | Published host port | `7849` |

## Persistence

Media index caches, SQLite database, and user playlists are stored in `./data/navidrome/data`.
Music tracks reside in `./data/navidrome/music` (read-only inside the container).
Data is preserved across upgrades and container recreation.
