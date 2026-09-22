# RSS-Bridge

Produce RSS/Atom from selected web pages and transform or merge existing feeds. FreshRSS is a reader; this service supplies feeds for it and other clients. Installing it creates no subscriptions or background scraping jobs.

## Configure a feed

Open http://localhost:11134, choose the CSS selector, XPath, feed merge, feed reducer or filter bridge, fill in the actual source and matching parameters, then request its RSS/Atom output. Validate that output before adding its URL to your reader. These five bridges are explicitly enabled in the initial config; other upstream bridges require an intentional allowlist change.

The bridge fetches source pages when clients request feeds, subject to its file cache. Poll at an interval appropriate to the source. Website layout changes, authentication requirements and upstream availability may break individual feeds even when the service is healthy. Source login credentials, proxies and external browser services are not configured.

For a FreshRSS container on ods-network, replace the localhost origin with http://rss-bridge and retain the generated query. For a host reader use localhost:11134. This is an explicit integration step; ODS does not create a reader account or subscribe a project automatically.

## Configuration and storage

rss-bridge-config stores /config/config.ini.php, copied into the application by native startup. The initial Docker volume receives the shipped config once; recreation preserves later owner changes. To edit it, copy the file out with docker cp ods-rss-bridge:/config/config.ini.php ./rss-bridge-config.ini.php, edit locally, copy it back to the same container path and restart the extension. Keep the PHP exit guard. An upstream image update does not overwrite the saved volume configuration.

rss-bridge-cache stores /app/cache. Back up configuration; cached feeds can be rebuilt. Custom bridge PHP files placed in /config are executable application code and must be deliberately reviewed, not copied from arbitrary feed content. No host directories or Docker socket are mounted.

The host binding is loopback-only and native authentication is initially disabled. Other ods-network containers can access it. This is for trusted local use: user-supplied URLs are server-side fetches, not a public anonymous fetch service. Configure native HTTP basic authentication in config.ini.php and TLS ingress before remote exposure. The default has 10-second request timeouts, one retry and a 10 MB response bound.

Native root startup prepares nginx and PHP-FPM; workers run as www-data. Preserve the entrypoint and writable filesystem. Limits are two CPUs and 1 GiB. The root-page PHP HTTP probe checks the app, not external feeds. No GPU or selected model is involved.

## Provenance and validation

Unlicense source at f4aa9e0419d79e9545c53a7231721680ed8bafd4 and official image digest pinned. amd64/arm64 manifests support Linux-container runtimes on Linux, Windows and macOS with named volumes. Application startup, real feed extraction/cache expiry, reader subscriptions, authentication, restore and platform execution remain unverified. No containers or models were started.

Sources: [pinned source](https://github.com/RSS-Bridge/rss-bridge/tree/f4aa9e0419d79e9545c53a7231721680ed8bafd4), [configuration](https://github.com/RSS-Bridge/rss-bridge/blob/f4aa9e0419d79e9545c53a7231721680ed8bafd4/config.default.ini.php).
