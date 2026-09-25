# CloudBeaver Community for ODS

Browser database workspace for schema browsing, SQL editing and query results.
This recipe uses the Apache-2.0 Community Edition, not Enterprise or Team Edition.
It does not include commercial connectors, enterprise MCP features or a database
server for your application data.

## First configuration

Open `http://localhost:11057` and complete CloudBeaver's setup wizard, including
your administrator name and password. No preset administrator credentials are
supplied. Anonymous access is disabled, and the initial connection catalog is
empty. Configure users and permissions before sharing the workspace.

Create a connection to a database you actually want to access. Provide its
hostname, database name and credentials in CloudBeaver. A database on
`ods-network` is reached by its service name and internal port; `localhost` here
would mean the CloudBeaver container. Access to a host database depends on the
Docker host adapter and database listener/firewall configuration; this recipe
does not assume a Windows-only hostname or expose existing databases.

Test the selected connection, then browse schemas or explicitly run your SQL.
Queries execute against the real database, including writes when your database
account permits them. Use an account with the permissions you intend. ODS does
not reuse a database secret, scan every container or install sample databases.
Private/custom connections are enabled; anonymous connection grants and public
credential saving are disabled. Other saved credential behavior is managed by
CloudBeaver's administrator configuration.

## Storage and drivers

`cloudbeaver-workspace:/opt/cloudbeaver/workspace` retains the internal H2
configuration database, users, connection definitions and workspace resources.
It is not a backup of the databases you connect. Stop the service before backing
up the workspace, preserve UID/GID 8978 and retain backups before upgrades.
Disabling the extension must preserve the volume.

The image contains its Community drivers. Additional supported driver downloads
can contact their vendor repositories and may have separate licensing terms.
The image's driver directory is writable by the application; downloads there
are not an additional persistent volume and may need to be repeated after
container recreation. Workspace resources remain persistent. No arbitrary
third-party driver or commercial connector is installed by this recipe.

The empty initial connection file is copied only by upstream first-run setup;
it does not erase existing connections in a restored workspace. Review permissions
and credentials already present in any workspace you restore.

## Runtime and evidence

Community version **25.3.5** is pinned by digest and matched to its repository tag;
it is not presented as a verified latest release across every distribution channel.
Published images support Linux amd64 and arm64. Windows/macOS use a Linux Docker
engine. The original launch script detects UID 8978 and directly runs the server,
so root ownership repair and `su` are unnecessary at runtime. Build-time ownership
preparation and Docker init preserve the upstream startup behavior.

The JVM heap is limited to 1536 MiB within a 2 GiB container limit, with two CPUs.
Large result sets may require explicit resource and query-limit adjustments.
No GPU, model or context configuration is changed. Only loopback 11057 is
published; remote use requires deliberate HTTPS and access configuration.

The curl probe checks HTTP availability, not database connectivity. **Image
build, wizard/account creation, driver loading, SQL execution and workspace
restart/restore remain runtime-unverified.** Schema/Compose/staging checks are
separate. No application container, database query or inference was started.

- [Community source](https://github.com/dbeaver/cloudbeaver/tree/25.3.5)
- [First steps](https://dbeaver.com/docs/cloudbeaver/Getting-started/)
- [Server configuration](https://dbeaver.com/docs/cloudbeaver/Server-configuration/)
