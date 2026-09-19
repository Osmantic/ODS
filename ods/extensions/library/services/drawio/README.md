# draw.io — local diagrams

Create diagrams in your browser and keep their editable `.drawio` files on your device. This optional recipe pins the official 31.4.5 image by digest, runs as its Tomcat UID 1001 and exposes only the HTTP listener on loopback port 8106. Its content policy limits network resources to the local origin, disables object embeds and allows local data/blob images. AI generation is disabled; no cloud credentials or export server are configured.

## Enable and use

Copy this directory to `extensions/services/drawio/` and run `./ods enable drawio`. Open `http://localhost:8106/?offline=1&https=0`; the manifest uses the same launch path. `offline=1` disables cloud storage choices and `https=0` supports this local HTTP listener. Set `DRAWIO_PORT` in `.env` before enabling to change the published port.

Create a diagram, choose Device when selecting storage and save the `.drawio` file. Reopen that file to continue editing. Use browser SVG/PNG export when sharing an image; keep the editable original separately. PDF uses the browser print workflow, which is not covered by the automated test. This recipe has no server-side document library, account system, cloud synchronization or collaborative editing service. Browser storage can be cleared, so save important work as files and include those files in your backups.

External fonts, image URLs, cloud integrations and AI requests are intentionally unavailable under the local content policy. Imported diagrams that depend on remote assets may render differently; embed necessary assets or use local fonts. The vendor container needs a writable filesystem to generate its JavaScript/Tomcat configuration and self-signed certificate at startup, so `read_only` is not enabled. Its internal HTTPS port is not published. The generated certificate is not a deployment trust configuration; use your existing authenticated HTTPS reverse proxy for remote access.

## Verification and rollback

`ODS_TEST_DRAWIO_BROWSER=1 python -m pytest -q tests/test_drawio_extension.py` uses the installed recipe, real Chromium and the editor's JSON iframe protocol. It loads a two-node diagram, changes a label with normal editing controls, saves with Ctrl+S, exports SVG, reloads the editor and reopens the saved XML. It checks the rendered labels, exported SVG, effective content policy and absence of external HTTP requests in that flow. The test supplies only the small parent page; the editor, serialization and SVG renderer come from the pinned image.

Install pytest, PyYAML and Playwright 1.62.0 with Chromium to run the test. Linux amd64 is the automated target. ARM, Safari/Firefox, OS file pickers/print dialogs, large diagrams and remote proxy configurations remain unverified. Disable with `./ods disable drawio`; downloaded diagrams remain on your device. To roll back an image update, restore the previous pinned recipe and recreate the service, retaining original diagram files in case a newer file feature is not understood by an older editor.

References: [official Docker deployment](https://github.com/jgraph/docker-drawio), [local storage mode](https://github.com/jgraph/docker-drawio#quick-start), [JSON embedding protocol](https://www.drawio.com/docs/reference/embed-mode/).
