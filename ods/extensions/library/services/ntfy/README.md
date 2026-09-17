# ntfy — local workflow notifications

Publish completion and failure notices from n8n or local scripts without a hosted notification account. This optional extension provides an HTTP API and web inbox. Topic access is denied until you create an account; no default password is shipped.

## Install and create an account

Install **ntfy (Local Notifications)** from the dashboard's Extensions catalog, then enable it. The web app and public health endpoint are at `http://localhost:8097`; health does not mean an operator account exists.

Create the first administrator interactively on the ODS host:

```bash
docker exec -it ods-ntfy ntfy user add --role=admin operator
```

Enter the password at the prompts, then sign in to the web app. Do not put a password in a workflow URL. For automation, create a separate user and grant access only to its topic:

```bash
docker exec -it ods-ntfy ntfy user add automation
docker exec ods-ntfy ntfy access automation ods-jobs rw
```

In an n8n HTTP Request node, use `POST http://ntfy:8080/ods-jobs`, store Basic Auth credentials in n8n's credential store, and send the notification text as the request body. Subscribe to `ods-jobs` in the web app using the same authorized account. From the host, `curl --user automation --data 'Local job finished' http://127.0.0.1:8097/ods-jobs` prompts for a password.

## Configuration and lifecycle

| Variable | Default | Purpose |
| --- | --- | --- |
| `NTFY_PORT` | `8097` | Published host port; container listener stays `8080` |
| `NTFY_BASE_URL` | `http://localhost:8097` | Client-facing URL; update when changing port or hostname |

The image is pinned to ntfy **2.28.0** and its multi-architecture digest. CPU only; the image publishes Linux amd64 and arm64 variants. Bind defaults to loopback. Use an authenticated HTTPS access path before exposing credentials over a network. Remote proxy integration is operator-owned; this extension does not change ODS routing or firewall settings.

Authentication and the 24-hour message cache persist in `data/ntfy`. `ods disable ntfy` preserves them; enable again to resume. Stop the service before copying this directory for a consistent backup, and restore the entire directory while stopped. Do not delete it to reset a password: use `docker exec -it ods-ntfy ntfy user change-pass operator`.

No upstream relay, Firebase, email, attachments, or browser push service is configured. The local web inbox and API work without these; iOS background push and browser push are outside this extension's default contract. Notifications can contain sensitive text, so use brief job summaries and limit topic access.

References: [ntfy installation](https://docs.ntfy.sh/install/), [authentication and configuration](https://docs.ntfy.sh/config/), [2.28.0 release](https://github.com/binwiederhier/ntfy/releases/tag/v2.28.0).
