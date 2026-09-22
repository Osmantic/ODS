# Apache NiFi for ODS

Apache-2.0 flow-based data integration using the official amd64/arm64 image,
pinned by digest. Native HTTPS, single-user authentication and startup are kept.
No sample flows, external credentials or running processors are created.

## First use

Configure two independent random 64-character lowercase hexadecimal secrets:
`NIFI_ADMIN_PASSWORD` for the `ods` administrator and `NIFI_SENSITIVE_PROPS_KEY`
for encrypted properties stored in flows. Install `nifi` and open
`https://localhost:11146/nifi`. Native self-signed TLS requires browser trust for
this local instance; an internet-trusted certificate is not provisioned.

`NIFI_PORT` changes the published port. Update `NIFI_PUBLIC_URL` to the matching
HTTPS browser address when changing it. The native allowed proxy hosts track
the Compose port. A remote hostname requires explicit certificate and allowed
host configuration as well; do not simply expose the local service publicly.

Create a process group for the actual project, then configure processors,
connections, controller services, credentials and back-pressure according to
its data source/destination. Containers reach authorized ODS services through
their Docker DNS names and container ports. Host-local files are not automatically
mounted into NiFi; declare a scoped mount if the chosen processors need one.
The Portal does not receive administrator credentials or silently start a flow.

## Credentials and persistence

Native startup reapplies the administrator password on every restart. The flow
encryption key is different: never change it by replacing an environment value.
The wrapper rejects mismatches with the retained configuration before startup;
use NiFi's native migration tooling for an intentional key change.

Named volumes retain conf (including flow definition, keystores and security),
content, database, flowfile and provenance repositories, state, logs and native
NAR/Python extension directories. Back up the full consistent set after stopping
NiFi, with the encryption secret retained separately. Recreating a container
must keep these volumes; preserving only conf would lose queued flow content.

The initial credential is a single-user administrator, not a multi-tenant access
model. Configure native federated identity/authorization explicitly if required.
Custom processors can execute code with container permissions; none are installed
by this recipe. No host Docker socket or host-wide directory is mounted.

## Resources and readiness

Native UID1000 runs on Linux Docker Engine or Windows/macOS Docker Desktop Linux
containers. CPU-only: 512 MB initial/2 GB maximum Java heap within a 4 GB limit
and two CPUs. Initial startup can take minutes. The root filesystem remains
writable for native startup/unpacked extensions. Disk demand depends on flow
queue size and provenance retention; tune these for the real project.

Only HTTPS is published on loopback. Site-to-site/debug ports are not published.
Docker health queries the native access-configuration API over container-local
TLS; certificate validation is skipped only for this self-signed local probe.
It establishes application availability, not a successful login or working flow.

Build, browser TLS/login, flow execution, queue retention and restore/platform
checks remain pending. Schema/staging checks are separate from runtime evidence.
No containers or models were started during integration.
