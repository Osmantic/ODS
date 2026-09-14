# OpenSpeedTest

Measure the connection between a browser and the ODS host before diagnosing slow
document uploads, remote dashboards or large file transfers. This is a local
network test, not a measurement of the host's Internet connection or LLM speed.
The recipe pins upstream OpenSpeedTest v2.0.6 by digest and uses its unprivileged
NGINX image. No database, account, model or GPU is required.

## Start and use

Install OpenSpeedTest from the Extensions library and open
`http://localhost:7825`. Start a test in the browser; it sends synthetic download
and upload data to this container. `OPENSPEEDTEST_PORT` changes the published port.

The default loopback binding measures only the local browser-to-container path.
To measure another device's connection, deliberately configure access through a
trusted network or protected reverse proxy and open the ODS host's address on
that device. This application has no login. Do not expose an unrestricted test
endpoint to the Internet: tests consume bandwidth and can compete with other ODS
services. Stop the extension when measurements are complete.

A proxy, Wi-Fi link, browser, virtual network, CPU limit or another transfer may
limit the result. The recipe caps the service at two CPUs and 512 MiB; a result
does not certify the physical NIC's maximum throughput. Compare repeated tests
under similar load. Use direct HTTP/1.1 to the published port when evaluating the
recipe's baseline; proxy buffering and HTTP/2 or HTTP/3 can distort upload timing.

The image's optional ACME client is disabled. Only its HTTP port is published;
the bundled HTTPS listener and certificate are not exposed. Configure your own
TLS termination if needed. Upstream links in the page can open external sites.

## Lifecycle and verification

There is no persistent result database in this recipe. Record the displayed
measurements yourself; recreating or disabling the container does not preserve a
test history. Roll back by restoring the previous image digest or removing the
optional extension. There is no application data migration.

The opt-in HTTP test starts the exact recipe, verifies the served application,
checks the download response is uncompressed and non-cacheable, sends a real
upload body, and repeats after recreation. These checks verify the transfer
endpoints, not the accuracy of a browser's timing algorithm. Browser measurements,
physical LAN/Wi-Fi comparisons, TLS proxies and native ARM/macOS/Windows activation
need separate verification.

Upstream: [Docker image](https://hub.docker.com/r/openspeedtest/latest),
[source and deployment](https://github.com/openspeedtest/Docker-Image),
[measurement application](https://github.com/openspeedtest/Speed-Test).
