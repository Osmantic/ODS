# File Browser

A web file manager rooted at the ODS `data/` directory — browse, upload, download, and share files without SSH.

## Setup

Set `FILEBROWSER_ADMIN_PASSWORD` in `.env` before enabling **File Browser** from Extensions, then visit `http://localhost:8087` and sign in as `admin`.

## Settings

| Variable | Default | Behavior |
| --- | --- | --- |
| `FILEBROWSER_PORT` | `8087` | Published HTTP port |
| `FILEBROWSER_ADMIN_PASSWORD` | `—` | Initial admin password; change it in the UI after first login |
| `BIND_ADDRESS` | `127.0.0.1` | Shared ODS bind address; set `0.0.0.0` for LAN access |

## Notes

- The container sees only `./data` — the rest of the install tree (compose files, `.env`) is not mounted.
- Change the admin password in the UI after first login; the env value seeds the initial account only.
