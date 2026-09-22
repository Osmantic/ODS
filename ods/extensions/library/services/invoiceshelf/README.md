# InvoiceShelf

Native invoices, estimates, customer records and PDF documents. Official AGPLv3
stable 2.4.2 image pinned for amd64/arm64; no 3.x preview or optional module is installed.

## First setup

Provide a random 64-hex `INVOICESHELF_KEY` before installation. It is converted into
Laravel's 32-byte base64 application key without printing it. Keep this secret stable
across updates/restores. Losing or replacing it can make encrypted settings unreadable.
It is not the administrator password.

Open `http://localhost:11122` and complete the native setup wizard. Choose SQLite and
keep `/var/www/html/storage/app/database.sqlite`, then create the administrator and
company profile. No administrator, customer, invoice, SMTP or payment provider is
created by ODS. Email delivery and payment collection require separate configuration;
installation never sends a message or issues a charge.

Use the exact configured hostname `localhost` because session and Sanctum origins
match it. If changing the public address, update APP_URL, SESSION_DOMAIN and
SANCTUM_STATEFUL_DOMAINS together and restart. Remote use also needs explicit TLS and
network configuration; do not expose the initial setup wizard to untrusted clients.
Project-specific API integration and Portal association remain separate work.

## Persistence and upgrades

`invoiceshelf-storage` persists SQLite, uploaded/generated documents and application
storage. `invoiceshelf-modules` persists the upstream modules directory; none is
installed automatically. Back up these volumes with the service stopped and keep
the encryption key separately. A copy of the database alone is not a complete backup.
Use the native upgrade/migration procedure when changing versions; automatic Laravel
migrations and optimization are disabled as in the upstream production example.

The official www-data user, writable application filesystem and S6 startup are
preserved, including storage initialization. The service has a 2 GB memory limit
and host port 11122 bound to loopback; other ODS-network containers can reach it.
SQLite is appropriate for small deployments. Reassess the database and resource
configuration for larger teams instead of treating this recipe as an HA deployment.

## Compatibility and verification

Official amd64/arm64 images support compatible Docker runtimes on Windows, Linux and
macOS without a GPU/model. HTTP health verifies the application responds, including
the initial wizard; it does not establish completion of setup or PDF generation.
Image build, login, documents, persistence, migrations and restore remain pending
runtime checks. No services/models were started during preparation.

Sources: https://docs.invoiceshelf.com/install/docker.html,
https://github.com/InvoiceShelf/docker and
https://github.com/InvoiceShelf/InvoiceShelf/tree/a820744cf07277a5c1daf596e279d88a001eb6fa.
