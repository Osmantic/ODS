# Open WebUI — ODS branding

How ODS brands the Open WebUI chat surface that users see daily.

## What's branded today

Set via env vars in `docker-compose.base.yml`. These flow into Open WebUI's runtime config and surface in the page title, PWA install dialog, and share links.

| Variable | Default | What it controls |
|---|---|---|
| `WEBUI_NAME` | `ODS` | The display name. Appears as the browser tab title, the header inside the chat UI, and — critically — the label next to the ODS icon when the user adds the chat to their phone's home screen. |
| `WEBUI_URL` | empty | Optional public URL Open WebUI uses for share links, OAuth callbacks, and PWA install metadata. Leave empty for traditional localhost usage. Headless/proxy installs should set it to `http://chat.${ODS_DEVICE_NAME}.local` after ods-proxy + mDNS are enabled; tunnels should use their public URL. |
| `ODS_DEVICE_NAME` | `ods` | The mDNS hostname segment. Drives `${...}.local` and is reused by future remote-access integrations. |

After opening a reachable chat URL, users see "ODS" everywhere instead of "Open WebUI", and adding the PWA to a phone's home screen produces a tile labeled "ODS".

## What's not branded yet (follow-up work)

Open WebUI's PWA manifest pulls its name from `WEBUI_NAME` (✓) but its icons and theme color from static assets bundled inside the container image. To fully match the ODS brand, a follow-up PR needs to:

1. **Override `/static/favicon.png`, `/static/splash.png`, `/static/logo.png`** — Open WebUI serves these from `/app/backend/static/`. Mounting a ODS-branded set via a volume mount in the compose service is the cleanest path:
   ```yaml
   volumes:
     - ./extensions/services/open-webui/branding/favicon.png:/app/backend/static/favicon.png:ro
     - ./extensions/services/open-webui/branding/logo.png:/app/backend/static/logo.png:ro
     - ./extensions/services/open-webui/branding/splash.png:/app/backend/static/splash.png:ro
   ```
2. **Source actual icon assets.** Sizes needed:
   - `favicon.ico` — 16x16, 32x32, 48x48 (multi-resolution)
   - `apple-touch-icon.png` — 180x180
   - `pwa-192.png` — 192x192 (Android home screen)
   - `pwa-512.png` — 512x512 (PWA splash + larger displays)
   - `pwa-maskable-512.png` — 512x512 with safe-zone padding for adaptive icons
   - `splash.png` — 1024x1024 brand splash for iOS PWA launch
3. **Set `theme_color`** to a ODS brand color (currently Open WebUI defaults to its own dark gray). This may require a custom `manifest.json` override served via the same volume-mount approach if Open WebUI doesn't expose a theme-color env var.

## Where future assets should live

```
extensions/services/open-webui/
├── BRANDING.md       (this file)
├── manifest.yaml
└── branding/         (new — added when icon assets are produced)
    ├── favicon.ico
    ├── favicon.png
    ├── apple-touch-icon.png
    ├── pwa-192.png
    ├── pwa-512.png
    ├── pwa-maskable-512.png
    └── splash.png
```

Asset production needs design input (ODS logo, brand palette) before this directory should be populated. Placeholder assets risk shipping in a release if not caught.

## Testing the current PR

After this PR merges, on a running ODS:

1. Browse to the reachable chat URL: `http://localhost:3000` for traditional local usage, or `http://chat.ods.local` after enabling ods-proxy + mDNS.
2. The browser tab should read "ODS" — not "Open WebUI".
3. On a phone, after adding the page to the home screen, the icon label should read "ODS".
4. Inside the chat UI, the top-left header should read "ODS".

The icon next to the label still shows Open WebUI's default until the follow-up PR ships the asset overrides.
