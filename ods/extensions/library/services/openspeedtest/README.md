# OpenSpeedTest

Browser-based download/upload throughput and latency measurement against this
ODS host. MIT upstream static application, source commit and archive SHA256 pinned.
The upstream ready-made image currently contains nginx 1.26.2; this recipe instead
packages the verified official source on the pinned nginx 1.28 Alpine base.

Open `http://localhost:11127` and start a test explicitly. No test runs during
installation or health checking. From the same computer, results reflect the
local browser/container path, not ISP bandwidth. To measure Wi-Fi/LAN, deliberately
bind the published port to the intended LAN interface and permit it through the
host firewall, then open that host's address from the other device. Do not interpret
a loopback test as a measurement of your internet connection.

The service is unauthenticated and starts bound only to host loopback. Other ODS
containers can reach it on the shared network. No public tunnel, firewall rule,
router change, certificate or cross-origin permission is configured automatically.
Use a direct connection where possible: proxies, TLS termination, VM networking,
browser limits and CPU contention can materially affect the result. A reverse
proxy must allow at least 35 MB request bodies, disable buffering/compression for
measurement paths and preserve suitable timeouts. The upstream HTTP/1.1 upload
pattern is retained; HTTP/2/3 needs separate upload handling.

Native relative `downloading` and `upload` endpoints target this instance. Result
database submission is disabled in the pinned upstream page. There is no account,
history database or persistent volume. Reload/recreation does not preserve a
measurement. The test intentionally sends substantial traffic; choose test duration
in the app and avoid running it during other bandwidth-sensitive work.

Runs as nginx UID/GID 101 with a read-only root, bounded temporary request storage,
512 MiB memory/two CPUs and no host mounts/GPU. Linux amd64/arm64 containers use
Docker Engine or Windows/macOS Docker Desktop. HTTP health checks only fetch the
page and do not measure throughput or claim correct performance.

Source/archive and configuration checks are complete. Image build, upload/download
behavior, actual measurements and cross-platform runtime remain pending. No service
or model was started and no speed test was performed.

Sources: [application](https://github.com/openspeedtest/Speed-Test),
[upstream deployment and proxy guidance](https://github.com/openspeedtest/Docker-Image).
