# Vaultwarden

Unofficial Bitwarden-compatible password and secure-note vault. AGPL-3.0 server
1.37.3, official Alpine image pinned by digest with its bundled web vault.
This is an owner-operated vault, not automatic credential injection into Portal,
model prompts or other extensions. No vault secrets are imported by ODS.

## First account

Configure `VAULTWARDEN_ADMIN_TOKEN` with a randomly generated 64-hex secret. This
is the administrator access token, **not** the master password of a vault account.
Open `http://localhost:11124/admin`, authenticate with the token and invite the
owner's email address. With SMTP absent, open
`http://localhost:11124/#/signup` and register using that exact invited address.
Choose the master password yourself. Uninvited registration and organization
invitations are disabled; the server administrator can still invite explicitly.

The recipe uses the native supported plain token format with 256 random bits.
Vaultwarden logs a recommendation to use an Argon2 token; that warning is not an
installation failure. The token is visible to administrators with Docker inspect
access. Never substitute a human password or copy it into chat. This recipe's
validator accepts exactly 64 hexadecimal characters, not an Argon2 PHC string.

The browser vault requires a secure context. The local `http://localhost` origin
is suitable; a LAN IP over HTTP is not. Access from other devices requires a
deliberately configured HTTPS reverse proxy, canonical DOMAIN and matching
client server URL. No public route, certificate or mobile push service is set up.

## Persistence and configuration

The `vaultwarden-data` named volume stores SQLite, signing keys, attachments and
sends. It is owned by UID/GID 1000 with a private directory. Preserve all of it;
do not back up only the database. Stop the service before copying the entire
volume, or follow upstream's consistent online backup procedure. Keep an offline
recovery/export plan and the account's recovery information. Server administrator
access does not replace the user's master password.

Admin-page settings saved in `/data/config.json` override environment settings.
After changing ports, DOMAIN, registration policy or the admin token, reconcile
those saved settings; changing only the environment may not take effect. Owner
changes are preserved, never silently removed at startup. No automatic master
password reset, user creation or vault unlock is attempted.

SMTP, external site-icon downloads and mobile push are disabled/unconfigured.
Password hints are not exposed. Configure actual mail credentials if invitations
or recovery emails are needed. Native `/alive` checks availability, not successful
account creation, encryption, client synchronization or recoverability.

## Platform and limits

Linux amd64/arm64 containers on Docker Engine or Windows/macOS Docker Desktop.
No GPU, model backend or host bind paths. Host port 11124 is loopback-only;
other ODS containers on the shared network can reach port 8000. The app uses
upstream startup/health scripts as UID 1000, a read-only root, temporary storage,
one persistent volume and limits of 1 GiB/two CPUs. No Docker socket access.

Image build, initial invite/login, vault writes, synchronization and recovery on
each platform remain runtime validation pending. No service or model was started.

Sources: [release source](https://github.com/dani-garcia/vaultwarden/tree/1.37.3),
[registration and admin invitations](https://github.com/dani-garcia/vaultwarden/wiki/Disable-registration-of-new-users),
[administration](https://github.com/dani-garcia/vaultwarden/wiki/Enabling-admin-page),
[backup](https://github.com/dani-garcia/vaultwarden/wiki/Backing-up-your-vault).
