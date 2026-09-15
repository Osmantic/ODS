# IT-Tools local developer utilities

Serve IT-Tools from ODS for browser-based encoding, text and structured-data
conversion. Base64 and JSON-to-YAML conversions are verified without network
access after the tool page loads. This adds no model service or conversion API.

Install from Extensions, or copy the entire directory to
`extensions/services/it-tools` in the active installation, then run:

```bash
ods enable it-tools
```

Open `http://localhost:8101`. Set `IT_TOOLS_PORT` before enabling if that host
port is occupied; the container listener remains 8080. The library's nginx
configuration must be copied alongside compose. It supplies SPA route fallback
and lets the static image run as UID/GID 101 with a read-only root filesystem,
no Linux capabilities, 128 MiB memory and temporary files only in `/tmp`.

## Local processing and access

There are no server accounts. Loopback is the default access boundary; remote
use requires your protected ODS access path. Clipboard APIs may require HTTPS
when opened from another device. Browser storage holds preferences; there is no
server-side document store or volume to back up. Treat downloads you create as
your own files and save them before closing the browser.

The response policy restricts script, connection and font requests to the local
origin, while allowing inline styles and generated data/blob images. Tools that
need external resources will be blocked by this policy. External documentation
links remain ordinary navigations that the user can choose to open. This policy
does not turn an unreviewed script or pasted content into trusted material.

The pinned upstream release is **2024.10.22-7ca5933**, its latest published
release when this recipe was added. The digest in compose locks the exact
multi-architecture image, rather than tracking the upstream `latest` tag.
Only Base64 and JSON-to-YAML were exercised as browser workflows; not every
upstream utility has been certified by ODS. Reverting the recipe removes the
static service and requires no data migration.

## Validation

The opt-in test starts the copied recipe with an ephemeral loopback port,
validates deep-link serving and headers, then uses Chromium to perform Unicode
Base64 encode/decode and JSON-to-YAML conversion offline. Normal tests verify
generated catalog discovery without requiring Docker or Playwright.

```bash
python -m pip install 'playwright==1.62.0'
python -m playwright install chromium --with-deps
ODS_TEST_IT_TOOLS_BROWSER=1 pytest -q tests/test_it_tools_extension.py
```

Live coverage is Linux amd64 Docker/Chromium on WSL. ARM, macOS, Windows browser
clipboard behavior, mobile layout and every other utility remain unverified.
Source: [release](https://github.com/CorentinTh/it-tools/releases/tag/v2024.10.22-7ca5933),
[tracker configuration](https://github.com/CorentinTh/it-tools/blob/v2024.10.22-7ca5933/src/config.ts).
