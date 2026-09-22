# LLDAP for ODS

A lightweight directory with web-managed users/groups and LDAP bind/search.
This is not an OpenID Connect server, Active Directory replacement or automatic
login integration for Portal. Applications must explicitly configure LDAP support.

## Setup

Install `lldap` with `LLDAP_ADMIN_PASSWORD` (12+ characters), `LLDAP_JWT_SECRET`
and `LLDAP_KEY_SEED` (independent random values, 32+ characters each), plus your
chosen `LLDAP_BASE_DN`. Open `http://localhost:11066` and sign in as `admin`.
Create the required users and groups in the directory UI. Keep the secrets stable.
`LLDAP_PORT` changes the UI port; `LLDAP_BIND_PORT` changes the loopback LDAP port
from 11067. Internal ports remain 17170 and 3890.

For an explicitly configured ODS consumer on `ods-network`, the LDAP host is
`lldap:3890`; local host applications can use `127.0.0.1:11067`. The users base is
`ou=people,<your base DN>` and groups base `ou=groups,<your base DN>`. Configure a
dedicated account and appropriate groups for each consumer rather than sharing
the administrator password. LDAP access is unencrypted in this local recipe;
remote use requires an explicitly configured secure transport and access policy.
No LDAP settings, OS accounts or passwords in existing ODS apps are modified.

## Credentials and persistence

`lldap-data` stores the SQLite database and local TOML settings. The default file
is copied only when missing. Environment settings override their corresponding
TOML values. The root-only upstream ownership/gosu wrapper is replaced with a
nonroot bootstrap; the actual LLDAP server and health command remain upstream.

The initial password does not reset an existing administrator. Forced reset is
explicitly disabled. Back up the stopped database volume together with the saved
key seed and signing secret: the seed protects stored password material, and
changing it can make existing credentials unusable. Changing the JWT secret also
invalidates sessions. Preserve the base DN when reconnecting existing consumers.

SMTP password reset, LDAPS certificates, automated account bootstrap and external
identity integrations are not configured. Use the native administration interface
for users and consult upstream consumer examples when configuring another app.

## Compatibility and verification

Pinned GPL-3.0 official v0.6.3 image supports amd64 and ARM. Intended runtimes are
Docker Engine on Linux and Docker Desktop Linux containers on Windows/macOS.
No GPU, model, host socket or external database is needed.

The native health command checks HTTP/LDAP server availability. It does not prove
consumer login, directory authorization or recovery. Image build, first login,
LDAP bind/search, persistence and platform execution remain runtime-pending.
No application containers were started during integration.

Source: https://github.com/lldap/lldap/tree/v0.6.3
Consumer examples: https://github.com/lldap/lldap/tree/v0.6.3/example_configs
