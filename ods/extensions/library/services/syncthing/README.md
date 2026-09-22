# Syncthing

Synchronize deliberately selected folders between trusted devices using native Syncthing identities and encrypted connections. This is file synchronization, not a backup guarantee: configured peers can propagate edits and deletions.

## First setup

Set `SYNCTHING_GUI_PASSWORD` to 64 random hexadecimal characters. On a fresh volume, native `syncthing generate` receives it through stdin and hashes it for GUI account **ods**; the plaintext is not passed in command arguments. Open `http://localhost:11095` and sign in. The GUI is HTTP on host loopback; do not expose it remotely without configuring appropriate HTTPS and access controls.

The initial configuration contains no shared folders and no paired remote devices. Global/local discovery, relays, NAT mapping, telemetry, crash reporting and binary auto-updates are disabled at initial bootstrap. These owner-editable settings are not overwritten on later starts. Existing GUI credentials likewise remain authoritative after initialization; changing the bootstrap variable does not reset them.

## Files and device connections

Create a folder under `/var/syncthing/files`, explicitly add the intended peer by its verified device ID, and configure a reachable peer address such as `tcp://peer-address:22000`. Sharing requires configuring the intended folder/device relationship. With discovery and relays disabled, dynamic discovery alone will not connect devices.

The GUI port 11095 and TCP sync port 11096 are published on host loopback only. Another local client can use the latter. Cross-machine connections need an explicit reachable peer address for outbound connections, or deliberate firewall/listener configuration for inbound access. UDP discovery/QUIC is not published. The recipe does not open firewall ports or connect third-party relay services automatically.

Only `syncthing-data` is mounted; personal folders, Docker sockets and Playground projects are not exposed. To synchronize an existing project, deliberately arrange access to that folder and review sync direction, exclusions and file versioning first. No file copy, device invitation or project association is performed by installation.

## Persistence and recovery

The volume contains the device identity keys, configuration, index database and explicitly populated files. Back it up with the service stopped. Keep identity keys private and do not run cloned identities simultaneously. Preserve `.ods-initialized` when restoring this recipe's complete volume. An imported configuration without the ODS initialization marker is refused rather than silently resetting its folders or GUI password; migrate such data explicitly.

Runs as UID/GID 1000 with a read-only base, writable named volume/tmp, 1 GiB RAM and two CPUs. Data size and hashing workload can require higher limits. No GPU or model settings change. Native GUI health establishes server availability, not synchronization completion or backup integrity.

## Provenance and verification

MPL-2.0 Syncthing 2.1.5 official image pinned for amd64, arm64 and ARM Linux containers. Windows/macOS can use a Linux-container Docker runtime; native peers on those systems connect through the same device protocol. Filesystem case sensitivity, watchers, path rules and actual transfer behavior still require platform-specific verification.

Build, bootstrap/authentication, restart preservation, device pairing, transfer/conflicts/deletions and restore remain runtime-pending. No container/model was started and no device was paired.

Sources: [versioned source](https://github.com/syncthing/syncthing/tree/v2.1.5), [configuration reference](https://docs.syncthing.net/users/config.html).
