# CyberChef

Build repeatable data-processing recipes by chaining operations such as From
Base64, Gunzip, decoding, hashing and format conversion. This complements simple
single-purpose utilities with an ordered recipe that can be saved and replayed.

```bash
ods enable cyberchef
ods disable cyberchef
```

Open `http://localhost:8103`; set `CYBERCHEF_PORT` to choose another published port.
The default bind is loopback. The optional service requires no GPU or database.

## Using recipes

Add operations to the recipe in the order they should run, then provide input.
For a Base64-encoded gzip payload, use **From Base64 → Gunzip**. Save the recipe
for reuse and export any output you need before closing or clearing browser data.
Recipe URLs can contain encoded input in their fragment: including input in a
copied URL also includes that data in browser history and wherever the URL is saved.

The container serves the application assets; recipe processing runs in your
browser. There is no ODS conversion API, account database or server-side input
archive. Browser memory/CPU, rather than the container limit, bounds large recipes.

## Runtime contract

The official `ghcr.io/gchq/cyberchef:11.4.0` image is pinned by digest. Nginx runs
as UID/GID 101 with a read-only root, no capabilities, a temporary writable
directory and a 256 MiB server limit. A single worker serves the static assets.

The supplied browser content policy allows local workers/evaluation used by the
recipe engine and restricts resource connections to this origin. Operations that
need external HTTP/DNS services are unavailable with this policy. This is a local
processing recipe, not an unrestricted network-analysis deployment. The tested
offline decoding/decompression flow does not certify every CyberChef operation.

Server recreation has no saved input or recipe state to restore. Keep browser
exports and saved recipes separately; reverting/disabling the service leaves
those files with you. There is no background content download or cloud-sync setup.

Upstream: [CyberChef](https://github.com/gchq/CyberChef),
[11.4.0 image source](https://github.com/gchq/CyberChef/blob/v11.4.0/Dockerfile).
