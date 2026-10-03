# Traccar

Native GPS device, route and geofence management, Apache-2.0 version 6.15.3.
Official amd64/arm64 Java image with a private PostgreSQL 16 companion. This adds
device telemetry; it does not track the ODS owner or create simulated locations.

## First setup and devices

Set `TRACCAR_DB_PASSWORD` to a random 64-hex secret before installing. Open
`http://localhost:11118` and complete native registration of the first administrator.
The database password is not a web login. Complete initial setup before admitting
untrusted clients, review server registration settings and disable additional
registration unless needed. No default administrator is created by this recipe.

Only the OsmAnd protocol is enabled, on container port 5055 / host port 11119.
Enroll each intended device in Traccar, then configure its matching identifier in
the client. Host ports bind to loopback: a phone on another machine cannot connect
until networking and TLS have been deliberately configured. Do not expose hundreds
of protocol ports; add only those required by actual devices. Device identifiers
are not equivalent to authenticated web accounts; restrict ingress appropriately.

No device, webhook, email recipient or notification channel is configured. Reverse
geocoding is disabled. The browser map provider can still receive tile requests;
select a suitable provider before importing confidential location data. Project
integration should use a scoped application account/API access, not administrator
credentials. Automatic Portal project association is not yet implemented.

## Persistence and resources

`traccar-db-data` holds accounts, positions and configuration. `traccar-data` and
`traccar-media` preserve application files. Back up PostgreSQL consistently and the
file volumes together; retain the configured database secret. Changing the secret
in Compose does not rotate an existing PostgreSQL user's password: perform native
database rotation and update both services together.

The application runs as UID/GID 1000 with a read-only root, 2 GB memory limit and
1,400 MB Java heap. PostgreSQL has a 1 GB limit and no published host port. Console
logs use Docker rotation. Position history needs a retention policy suited to the
deployment; memory limits do not cap disk consumption. For larger tracking workloads
review upstream TimescaleDB guidance rather than assuming this small installation
is sized for a fleet.

## Compatibility and validation

Docker-based amd64/arm64 installation targets compatible Linux, Windows and macOS
hosts; no GPU/model is needed. `/api/server` checks native API availability, not a
successful location update. Image build, initial registration, schema migration,
device ingestion and restoration remain pending runtime validation. No services,
models or tracking clients were started during preparation.

Sources: https://www.traccar.org/docker/,
https://www.traccar.org/configuration-file/ and
https://github.com/traccar/traccar/tree/v6.15.3.
