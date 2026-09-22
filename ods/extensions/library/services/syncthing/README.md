# Syncthing

Synchronize selected datasets, notes and generated artifacts between explicitly
paired devices. This optional service uses upstream Syncthing **2.1.5** at a pinned
multi-platform digest. It needs no GPU and runs as UID/GID 1000.

Set `SYNCTHING_ADMIN_PASSWORD` in the ODS installation `.env`, then install
Syncthing from Extensions. Open `http://localhost:7831` and sign in as `admin`
(or `SYNCTHING_ADMIN_USER`). The password is hashed before the first listener
starts; subsequent starts retain GUI changes, credentials, device identity and
folder configuration. Changing initial credentials in `.env` does not reset an
existing account. Use the authenticated GUI to change it.
Initial passwords must be a single line of at most 72 bytes, matching upstream's
bcrypt limit; invalid initialization does not publish a partial configuration.

## Pair a device and share a folder

1. On both devices, obtain the local Device ID from Actions -> Show ID. Verify
   IDs through a trusted channel, then add each ID on the other device.
2. Set an explicit device address such as `tcp://192.168.1.20:22000`. Public and
   local discovery, public relays, NAT traversal, crash/usage reporting and
   automatic binary upgrades are off in the initial configuration. No default
   folder or automatic sharing is configured. Administrators can change these
   settings in the GUI; this recipe is not an enforced network firewall.
3. Create a folder beneath `/var/syncthing/files`, share it with that device, and
   explicitly accept the folder on the other device. Choose send-only,
   receive-only or send-and-receive behavior deliberately. Enable file versioning
   when appropriate: ordinary synchronization propagates deletions and is not a
   backup.

The default host bindings are loopback: `SYNCTHING_PORT=7831` for management and
`SYNCTHING_SYNC_PORT=22000` for TCP synchronization. A second physical device
requires an operator-managed reachable bind or VPN/SSH tunnel and firewall
rules. Protect the HTTP management UI with a TLS proxy or encrypted tunnel when
accessed remotely. Syncthing authenticates peer device certificates independently
of the GUI password. QUIC/UDP, public discovery and relay ports are not published.

## State, limits and recovery

`data/syncthing` contains configuration, private keys, the index database and
`files/`. ODS prepares the bind mount for UID 1000. Keep the entire directory
private and on a local filesystem; do not mount a live database from another
application as a sync folder. This image is limited to 2 CPU and 1 GiB RAM;
large folder indexes may need operator-adjusted resources.

Back up the whole directory while the service is stopped. Before upgrading,
read upstream migration notes and retain a stopped backup. Reverting the image
alone may not downgrade the database; restore a matching backup with the service
stopped. Disabling the extension preserves the directory. A malformed existing
configuration fails visibly rather than resetting identity or credentials.

The REST API uses an API key from the authenticated GUI; do not publish it in
workflow files. Health uses the deliberately public `/rest/noauth/health` route.
Runtime tests cover two Linux amd64 containers with explicit pairing and actual
file transfer/recreation. Physical LAN/VPN behavior, browsers, native ARM/macOS,
large datasets and upgrades of an existing user database need separate validation.

Upstream: [documentation](https://docs.syncthing.net/),
[pinned source](https://github.com/syncthing/syncthing/tree/v2.1.5).
