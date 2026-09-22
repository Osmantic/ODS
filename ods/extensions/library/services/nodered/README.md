# Node-RED: local event flows

This optional extension runs the pinned Node-RED 5.0.7 image with separate native
authentication for the editor/admin API and deployed HTTP nodes. It provides
Node-RED's event-driven flow runtime; it does not migrate or modify n8n workflows.

## Enable

1. Prepare `data/nodered` for UID/GID 1000. On Linux, from `ods/`:

   ```bash
   sudo install -d -m 0700 -o 1000 -g 1000 data/nodered
   ```

2. Generate two **different** bcrypt hashes using the native interactive command
   (password entry is hidden):

   ```bash
   docker run --rm -it --entrypoint node nodered/node-red:5.0.7@sha256:a649dd711d55490151a2c39a8e48ad0c44325488fbc0e66315f2d2e19e5e1ace node_modules/node-red/red.js admin hash-pw
   ```

3. In Extensions, enter the complete hashes as `NODERED_ADMIN_HASH` and
   `NODERED_HTTP_HASH`, and a long random `NODERED_CREDENTIAL_SECRET`.
   Hashes must use bcrypt cost 8 or higher; malformed values fail startup visibly.
   When maintaining `.env` manually, single-quote the complete hash so its `$`
   characters are literal. Retain the encryption secret with private backups.
4. Enable Node-RED and open `http://localhost:1880/admin/`. The editor user is
   `ods`. Deployed HTTP In paths are below `/flows/` and require HTTP Basic user
   `workflow` with the second password. For example, a `/receipt` HTTP In node
   becomes `http://nodered:1880/flows/receipt` for trusted ODS containers.

`NODERED_PORT` changes the host port; `BIND_ADDRESS` defaults to loopback. The
native `/admin/auth/login` endpoint is a public login description and health
probe, not access to flows. Admin API clients obtain a native bearer token;
HTTP-node clients use their separate Basic credential. The editor may serve its
login assets without authentication; flow reads and deployments require it.

## Runtime contract

- The administrator can deploy executable Function, Exec and network nodes.
  Treat editor access as code execution inside this container, including access
  to its data and environment. No Docker socket or host workspace is mounted.
  Other protocols created by trusted flows are not covered by HTTP-node auth;
  do not deploy additional listeners for untrusted clients without their own
  authentication. This is not a tenant sandbox or an egress firewall.
- Native palette installation/update/upload, automatic missing-module install,
  Function external modules, diagnostics and projects are disabled. Core bundled
  nodes remain available. Extra packages require an intentionally reviewed image
  and configuration change. `--no-telemetry` disables native telemetry.
- The process uses UID 1000, a read-only root, a 1 GiB container limit, 512 MiB
  Node heap and 128 MiB temporary storage. Function timeout defaults to 10 seconds
  and HTTP request timeout to 30 seconds. These are operational defaults, not
  hostile-code isolation or a throughput guarantee.
- Native v2 flow deployment accepts a revision and rejects stale revisions with
  HTTP 409. The backend owns deployment/restart reconciliation. In-flight flow
  work is not a durable message queue and is not promised to survive interruption.

## Persistence, rotation and recovery

`data/nodered` contains `flows.json`, encrypted `flows_cred.json`, native settings
and sessions, and filesystem context. Filesystem context normally flushes on a
timer and on graceful shutdown; it is not a transaction log. A successful HTTP
response does not promise that context was fsynced against host power loss.

To rotate passwords, revoke existing admin API tokens through the native
`POST /admin/auth/revoke` endpoint, replace the appropriate bcrypt setting, and
recreate the service. Native bearer tokens expire after one hour. Changing a
password hash alone is not a claim that every previously issued token is revoked.
The HTTP-node password takes effect on recreation. Neither password rotation
requires changing the credential-encryption secret.

**Keep `NODERED_CREDENTIAL_SECRET` unchanged for existing flows.** A different
key prevents native credential decryption; deploying with the wrong key can lose
saved credentials. Stop the service before backing up the complete data directory
and the secret. Restore to a separate directory with UID/GID 1000 ownership using
the pinned image and original key, then verify an authenticated flow that uses a
saved credential. Disable the extension to stop it and retain data for recovery.
For an image rollback after an upgrade, restore a matching pre-upgrade backup.

## Qualification

```bash
python -m pip install pytest bcrypt==5.0.0
python -m pytest ods/tests/test_nodered.py -q
sudo --preserve-env=PATH env ODS_TEST_NODERED=1 python -m pytest ods/tests/test_nodered.py -q -s
```

Linux amd64 native tests deploy a flow through the admin API, reject anonymous
reads, wrong passwords and a stale deployment revision, execute Unicode HTTP
responses, and call an isolated local fixture using an encrypted HTTP Request
credential. They verify context/credential recovery after recreation and cold
restore, explicit token revocation, password rotation and flow deletion. No
notifications or credentials go to outside services. Browser interaction,
third-party nodes, other protocols, ARM/macOS/Windows runtime, load, crash recovery
and cross-version migration remain unqualified. Docker Desktop bind ownership
must be checked on the target host.

References: [native authentication](https://nodered.org/docs/user-guide/runtime/securing-node-red),
[5.0.7 settings contract](https://github.com/node-red/node-red/blob/5.0.7/packages/node_modules/node-red/settings.js),
[v2 flow deployment](https://nodered.org/docs/api/admin/methods/post/flows/).
