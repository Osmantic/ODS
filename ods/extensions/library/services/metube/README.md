# MeTube (Video Downloader)

A self-hosted web UI for yt-dlp. Paste a video URL, pick a format, and downloads land in `data/metube/downloads` on the host.

## Setup

No secrets required. Enable **MeTube** from Extensions and visit `http://localhost:8081`. Downloads write to `./data/metube/downloads` inside the install directory.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `METUBE_PORT` | `8081` | Published HTTP port |
| `METUBE_DOWNLOAD_DIR` | `/downloads` | Download directory inside the container |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- MeTube has no built-in authentication; keep the default localhost binding or route through `ods-proxy`.
- Downloading may be subject to the source site's terms of service — queue only content you are allowed to keep.
