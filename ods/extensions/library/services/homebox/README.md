# Homebox household inventory

Store equipment, locations, manuals and receipts locally. Homebox runs without a
GPU and exposes an authenticated API for inventory workflows. This recipe pins
official `0.26.2-rootless` by digest and runs as UID/GID 65532 with a read-only
root filesystem. SQLite and uploaded attachments share `data/homebox`.

## Install and create your account

1. Generate a server secret with `openssl rand -base64 48` and store it as
   `HOMEBOX_API_KEY_PEPPER` in the private ODS `.env`. Keep it stable: changing
   this secret invalidates all issued API keys. Homebox rejects values shorter
   than 32 bytes. Set `BIND_ADDRESS=127.0.0.1` and
   `HOMEBOX_ALLOW_REGISTRATION=true` **before** enabling this extension. Copy this directory into
   `extensions/services/homebox`, then run `ods enable homebox` and `ods up`.
2. Open `http://localhost:7834`, register your own account with a strong password
   and log in. Each independent registration creates its own group; use Homebox
   invitations when additional people should share your inventory.
3. Set `HOMEBOX_ALLOW_REGISTRATION=false` in `.env`, run `ods up` to recreate the
   service with the changed environment, and verify registration is closed while
   your existing login still works. Public registration is disabled by default;
   an empty installation needs the explicit temporary opt-in above. Valid group
   invitations can still allow registration under Homebox's own rules.

The default port is `HOMEBOX_PORT=7834`. Finish account creation and close public
registration before changing `BIND_ADDRESS` for a protected remote-access path.
Health checks report server availability, not account provisioning. Existing
credentials and inventory stay in the database across recreation.

Create locations and items, associate their hierarchy and upload manuals or
receipts. The pinned release uses the `/api/v1/entities` API (older `/items`
examples do not describe this version). Use the instance's API documentation
and account API keys for automation; keep keys and attachment tokens private.
Users in a group share its inventory, so groups are the relevant isolation unit.

## Data and updates

ODS prepares `data/homebox` for UID 65532. Back up that entire directory while
the service is stopped, including the SQLite database and attachment files.
Back up the private `.env` pepper separately with appropriate access controls;
the database alone cannot recover issued API keys without it. Do not back up
only the database or synchronize an active SQLite directory.
Keep a matching pre-upgrade backup: database migrations may require restoring
the backup as well as the previous image for rollback. `ods disable homebox`
stops participation in the stack and preserves data.

Analytics and GitHub release checks are explicitly disabled. SMTP, OIDC, MQTT
and remote object storage are not configured by this recipe; choosing additional
integrations in the upstream application changes its network behavior.

The regression runs the real pinned Linux amd64 image, registers two separate
groups, checks authenticated inventory and attachment access, closes public
registration and verifies persistence and API-key revocation after recreation.
The pinned upstream API returns 500 for a cross-group attachment lookup (without
file bytes), although cross-group inventory lookup returns 404. This recipe
does not repair that upstream status-code inconsistency. Browser rendering,
label printing, mobile scanning, other architectures, large uploads and
upgrades from existing Homebox databases require separate validation.

Upstream: <https://github.com/sysadminsmedia/homebox/tree/v0.26.2>.
