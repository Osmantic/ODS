# Radicale

Radicale provides local CalDAV calendars and CardDAV address books. Install it
from the Extensions library after setting `RADICALE_PASSWORD`. The account name
is `ods`; the password must contain 16 to 72 UTF-8 bytes. A generated ASCII
password avoids character-versus-byte ambiguity at the bcrypt limit.

Open `http://localhost:5232/.web/` to manage collections, or configure a DAV
client with `http://localhost:5232/ods/`. The web page manages collections;
use a calendar or contacts client to edit events and people. `RADICALE_PORT`
changes the published port. Connected ODS containers use
`http://radicale:5232/ods/` on `ods-network`. Clients and agent workflows must be
configured explicitly; installation does not import or connect existing accounts.

The listener is published on loopback by default. Use an authenticated TLS
access path when connecting remote clients. The public `/.web/` login page is
the health probe; calendar/address-book reads and writes require the password.
The `ods` account owns its collections and cannot write another user's tree.

## Data and lifecycle

Collections are stored under `data/radicale/collections`. The existing host
agent prepares `data/radicale` for UID/GID 1000 and copies the shipped startup
file from the extension into `config/radicale/`. The container runs as
`1000:1000`, with a read-only root filesystem and no added capabilities. If
running Compose manually, prepare those files and ownership first; the library
installer performs these steps through the existing authenticated host agent.

The startup process hashes the configured password into a private temporary
file and removes the plaintext variable before executing Radicale. The source
`.env` and Docker container configuration remain available to their respective
administrators. Recreate the service after changing the password, then update
every DAV client. The old credential stops working while existing collections
remain. This is a single-account service; there is no self-registration.

Disable/re-enable or recreation preserves the data directory. For a cold backup,
disable the service and copy the complete directory with its ownership intact.
Restore to UID/GID 1000 using the same pinned Radicale version first. Events and
contacts are stored as files without application-level encryption at rest;
protect the disk and backups accordingly. Removing this optional service does
not require deleting those files.

The recipe pins Radicale `3.8.0`, permits eight connections, limits a request
body to 8 MiB and gives a connection a 30-second timeout. The container limit is
512 MiB RAM and one CPU. Clients should honor ETags and conditional writes to
avoid overwriting newer revisions. Native calendar/contact applications and
high concurrency require their own qualification; the supplied tests exercise
the HTTP DAV protocols with synthetic records.
