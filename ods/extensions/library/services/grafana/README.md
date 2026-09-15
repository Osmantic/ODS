# Grafana local dashboards

Optional Grafana OSS 13.2.1 for visualizing metrics from sources you choose.
Grafana stores dashboard definitions, accounts and data-source settings; the
metrics remain in their source database. No collector, data source, cloud
account, alert destination or prebuilt dashboard is configured automatically.

## Start and connect a source

Set a strong GRAFANA_ADMIN_PASSWORD and a separate, stable GRAFANA_SECRET_KEY
of at least 32 characters before installing/enabling. Open
http://localhost:3033 and sign in as admin. GRAFANA_PORT changes the local port
and default root URL together. For remote access, deliberately configure
GRAFANA_ROOT_URL, a protected route and TLS; the default binding is loopback.

Add a data source through Grafana's Connections page using a URL reachable
from its container. For example, an independently installed VictoriaMetrics
service can use its internal URL http://victoriametrics:8428 with the Basic
authentication configured on that service. localhost inside Grafana refers
to Grafana's own container. Test the source before creating queries/panels.
Installing Grafana alone does not start collecting ODS telemetry.

Anonymous access and self-signup are disabled. Vendor usage reporting,
version/plugin update checks, feedback links, Gravatar and automatic plugin
preinstallation are disabled. Built-in data sources remain available.
Explicitly configured remote sources, plugins, alerts and browser resources
can still use the network; these settings are not an egress firewall.

## Credentials and persistence

The container runs as UID/GID 472 and ODS prepares data/grafana for that UID.
That directory holds SQLite state and plugin data; the image is read-only
with bounded temporary storage. The API health check indicates availability,
not that any selected data source is healthy.

SQLite uses its supported WAL mode. Keep the complete data directory together,
including any WAL/SHM files, and stop Grafana before copying a filesystem backup.
First startup runs database migrations and has a longer health-check grace period.

GRAFANA_ADMIN_PASSWORD seeds a new database only. Changing its environment
value does not reset an existing account. Manage established passwords using
Grafana account settings or its documented administrator recovery procedure.
The encryption key is passed to both encryption settings used by 13.2.1.
Retain it with the database: replacing it casually can make stored data-source
credentials unreadable. Key rotation requires Grafana's migration procedure.

Stop writes and the container, then back up data/grafana plus private
deployment configuration. Retain the exact prior image. Test a restored copy
before resuming writes. For rollback after a version upgrade, use the
matching pre-upgrade database and image; downgrading a migrated SQLite file
is not a recovery procedure. Disable/re-enable preserves the data directory.

## Validation limits

The opt-in ODS_TEST_GRAFANA=1 pytest ods/tests/test_grafana.py drill uses
isolated owned containers and data. It checks real HTTP authentication,
dashboard storage and queries against a local synthetic metrics endpoint
before and after recreation. It does not qualify a production metrics
database, browser rendering, remote TLS, alert delivery, native ARM/macOS/
Windows or a complete ODS installer/update/doctor cycle.

Sources: [Docker configuration](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/),
[13.2.1 defaults](https://github.com/grafana/grafana/blob/v13.2.1/conf/defaults.ini),
[database encryption](https://grafana.com/docs/grafana/latest/setup-grafana/configure-security/configure-database-encryption/).
