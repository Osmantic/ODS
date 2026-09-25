# Pixel Release Operator

A repository-native, narrowly scoped operator so routine 4.x activation, rollback, and
verification no longer need a password-backed `sudo` session from the deployment owner.

The owner account itself keeps **no generic NOPASSWD sudo**. A dedicated forced-command
transport identity logs into localhost SSH and may run only the fixed root helper through a
fixed sudoers surface.

## Security model

- **Transport identity**: a separate system user with the fixed real shell `/bin/sh`
  (sshd invokes a forced command through the account shell with `-c`, so a `nologin` shell
  would break it). Its home, `.ssh`, startup files, and `authorized_keys` are all root-owned.
  The only `authorized_keys` line is `restrict,command="/usr/local/libexec/pixel-release-dispatch"`
  with the single reviewed Ed25519 public key. Because the key is forced/restricted, it has
  no generic interactive or command session.
- **Dispatcher** (`dispatch.py`): parses `SSH_ORIGINAL_COMMAND` with a strict character
  allowlist (no shell, quoting, or operators), accepts only the exact release-operator
  grammar, and routes the one fixed helper through `sudo --non-interactive`.
- **Root helper** (`managed.py`): root-only. It can only install/remove the fixed reviewed
  gateway/courier templates, run the fixed `systemctl` verb allowlist against the configured
  units, run fixed permission probes as configured broker reader identities, and report
  read-only status. No arbitrary bytes, paths, units, users, commands, environment, shell,
  package install, reboot, or Docker.
- **Templates**: reviewed unit bytes are provisioned into the root-owned trust directory.
  An install request carries only a lowercase SHA-256; the helper requires it to match the
  root-owned template digest and atomically installs those exact bytes. Remove verifies the
  destination is either the configured template or the configured prior template.
- **Concurrency/evidence**: each operation runs under a single-operation `flock`, with
  bounded time/output and a minimal fixed environment. Every operation (success or failure)
  creates a root-owned, no-replace, canonical JSON receipt (O_EXCL + fsync file and dir)
  carrying schemaVersion, timestamp, operation/result identity, exit status, and digests of
  evidence or error only. Receipts never contain paths, stdout/stderr, user content, or
  credentials, and are reconciled by name + SHA-256.
- **Owner client** (`client.py`): explicit-argv localhost SSH via the absolute
  `/usr/bin/ssh` to the fixed `127.0.0.1` destination on the fixed port 22 with
  `StrictHostKeyChecking=yes` and a single root-owned
  `/etc/pixel-release-operator/known_hosts` pinned to the exact local Ed25519 host key (no
  fallback known-host stores, no `/dev/null`). The known_hosts entry is only valid for port
  22, so the client rejects every other port; the client call runs under a hard timeout
  that is treated as failure. It uses `BatchMode` (no password), `IdentitiesOnly`, no agent
  forwarding, no generic session, and a private key that must be owner-owned mode 0600.

## Opt-in provisioning (one time, as root)

Prepare a staging directory with the reviewed files, then run:

```
sudo deploy/release-operator/provision-target.sh \
  pixel-release-transport \
  "$(base64 -w0 <transport-public-key.pub>)" \
  /path/to/staging
```

`provision-target.sh` validates its arguments and the staging layout, cross-checks each
template's actual bytes against its configured SHA-256, installs helper/template/config
files (root ownership and modes are the immutability boundary; no `chattr +i` is applied so
reviewed re-provision updates stay possible), creates the transport identity with a
root-owned home and a single forced-command key, provisions the root-owned
`/etc/pixel-release-operator/known_hosts` from the exact local Ed25519 host key, validates
the sudoers with `visudo`, and grants NOPASSWD **only** to the fixed helper surface.

The staging directory must contain `managed.py`, `dispatch.py`,
`pixel_release_grammar.py`, `config.json`, and `templates/openclaw-gateway.service` and
`templates/pixel-web-courier.service` (plus optional `<unit>.prior` templates referenced by
`config.json`). Template SHAs in `config.json` must match the reviewed bytes; provisioning
validates the staged config and template digests before install, verifies the local
Ed25519 host public key is root-owned mode 0644 before pinning it, verifies exact
staged-to-installed SHA-256 equality for every expected installed file (helpers, config,
templates, grammar, and known_hosts) before granting sudoers, and re-validates the
installed root config/helper afterward.

Enable the operator in owner-private onboarding so every candidate configure and
reactivation regenerates the same trusted client settings:

```json
"releaseOperator": {
  "enabled": true,
  "user": "pixel-release-transport",
  "key": "/home/agent/.config/pixel-deployment/release-operator.key"
}
```

`./pixel configure --answers ...` validates the fixed transport user and absolute key path,
then writes `PIXEL_RELEASE_OPERATOR_ENABLED`, `PIXEL_RELEASE_OPERATOR_USER`, and
`PIXEL_RELEASE_OPERATOR_KEY` into the owner-only generated `.env`. Existing onboarding
without `releaseOperator` remains disabled and uses the password-backed sudo fallback.

The transport is fixed to `127.0.0.1` on port 22 (matching the port-22-only known_hosts
pin), so there is no SSH port setting to configure.

## Operator verbs (complete surface)

```
status
--validate-config
unit install gateway|courier <sha256>
unit remove gateway|courier
systemctl daemon-reload
systemctl enable|disable|restart|stop|is-active|is-enabled gateway|courier [--now]
probe ops|frontier
```

## Thin Pixel lifecycle and broker-byte operator (opt-in, separate runtime snapshot)

The thin operator adds fixed `bundle`, `service`, `reboot`, and `broker-bytes` verb surfaces routed by the
same managed helper and dispatcher to `pixel_operator.py`, which wraps the reviewed Node
lifecycle CLIs. It is provisioned separately after `provision-target.sh`:

```
sudo deploy/release-operator/provision-pixel-operator.sh \
  pixel-release-transport /path/to/bundle-staging /path/to/runtime-snapshot
```

The bundle staging directory must contain `managed.py`, `dispatch.py`, `client.py`,
`pixel_operator.py`, `pixel_release_grammar.py`, and `pixel-config.json`. `pixel-config.json`
must bind the SHA-256 of the `runtime-manifest.json` built by `build-runtime-snapshot.mjs`.
Provisioning first copies every required source into a freshly created root-private staging
directory (bounded, no-link, single-link) and binds each SHA-256; it then executes, installs,
and byte-compares only those root-private bytes. It installs the updated managed helper and
the owner-side client/grammar surface, installs the runtime atomically (retaining prior
versions), builds the exact final config once, and validates the sudoers with `visudo` before
atomically installing and re-validating it.

The thin operator surface (root grammar stays closed — no network, raw shell, arbitrary
executable/unit/path, or generic reboot verb):

```
bundle install|inspect|status|activate|remove goal|fleet-goal|deep-work-soak <sha256>
bundle rollback goal|fleet-goal|deep-work-soak <sha256> <sha256>
service status goal|fleet-goal|deep-work-soak <sha256>
reboot prepare goal|deep-work-soak <evidence-sha256>
reboot execute <grant-sha256>
reboot status|reconcile
broker-bytes backup|install|restore|verify
```

The `broker-bytes` verbs accept no paths, units, limb names, modes, digests, commands, or
bytes from the caller. The root-owned operator config fixes enabled limbs and destinations;
the manifest-bound runtime fixes candidate bytes; and the operator-private content-addressed
snapshot preserves the prior bytes for fail-closed restoration.

## Rollback and fallback

With the operator enabled, `apply.sh`, `rollback.sh`, and `verify.sh` route the privileged
unit install/remove, `systemctl` write verbs, and broker permission probes through the
operator client. Installations without the operator keep the existing password-backed
`sudo` path unchanged. Rollback installs the provisioned prior template (matched by SHA),
never arbitrary candidate bytes.

Legacy 3.2.2 migration remains a separate, one-time, human-authenticated `sudo` transaction
and is deliberately outside this operator's surface.
