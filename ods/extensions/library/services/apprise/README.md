# Apprise: fixed notification routes

Apprise API 1.5.4 exposes a single authenticated notification route for local
workflows. Operators configure destination URLs once; callers provide a message
and an optional native tag. The pinned image includes Apprise 1.13.1 and
Gunicorn 26.2.0. Native delivery stays synchronous: there is no durable queue,
exactly-once guarantee or delivery history provided by this recipe.

## Enable and call

In Extensions, supply:

- `APPRISE_AUTH_HASH`: a single bcrypt hash for user `ods`. A trusted local
  `htpasswd -nBC 12 ods` prompts for a password; retain only the hash after `ods:`.
- `APPRISE_DJANGO_SECRET`: a distinct random secret of at least 32 characters.
  This is Django configuration, not the caller's API credential.
- `APPRISE_CONFIG_TEXT`: native Apprise **TEXT** configuration containing only
  destinations you approve. It may contain credentials; store it as a secret.
  Native TEXT tag assignments let a caller choose among configured destinations.

For a local JSON receiver, a configuration line has this shape:

```text
workflow = json://your-local-receiver:9876/notify?retry=0&cto=3&rto=5
```

Replace that address with an actual approved receiver before sending anything.
Enable the extension. Clients use HTTP Basic user `ods` at
`http://localhost:8094/notify/ods`; trusted ODS containers use
`http://apprise:8080/notify/ods`. Send a JSON POST such as:

```json
{"title":"ODS workflow","body":"Artifact is ready","tag":"workflow"}
```

Use `Accept: application/json` to receive the native delivery result. HTTP 200
reports native provider success. HTTP 424 means at least one delivery failed;
another destination may already have received the message. It does not guarantee
human receipt. A timeout or interrupted process has an uncertain delivery outcome.
Callers must decide how to handle duplicates; do not automatically retry a whole
multi-target request on the assumption that nothing was sent.

`APPRISE_PORT` changes the host port. `BIND_ADDRESS` defaults to loopback. Add
separately reviewed TLS termination for remote/untrusted transport. Destination
URLs determine outbound traffic, including external notification providers if an
operator configures them. Enabling the extension itself sends no test notification.

## Runtime boundary

The gateway allows only POST `/notify/ods` (with optional trailing slash) and
public GET `/status` for readiness. Stateless notification, other configuration
keys, configuration read/write/list, plugin details and browser pages return 404.
The native backend additionally enables configuration lock and API-only mode.
Client `urls` does not replace the stored route; tags choose within that route.
Attachments and HTTP redirect following are disabled, custom plugin directories
are empty, and recursive configuration imports are disabled.

NGINX runs UID 101 and writes its password file 0600 in private temporary storage.
The native WSGI app runs UID 1000, binds **127.0.0.1:8000 inside the gateway's
network namespace**, and has no independently published port. Other ODS peers
reach authenticated port 8080. The API retains outbound network access to reach
approved targets. Docker administrators and destination operators remain trusted.

The recipe uses the shipped Gunicorn configuration and WSGI application directly,
with one synchronous worker and a 30-second timeout. It does not run the image's
second NGINX/supervisor layer. Gunicorn's management control socket is disabled.
PID handling uses Compose init. Both containers have
read-only roots, dropped capabilities and bounded temporary storage; gateway/API
memory limits are 128/512 MiB. Requests are limited to 64 KiB at the gateway.
Native optional provider-specific retries remain an operator URL setting; the
qualified example sets `retry=0` and bounded connect/read timeouts.

## Configuration, rotation and recovery

The operator's configuration is validated by the pinned native parser and written
to a private temporary `ods.cfg` at startup. At least one usable native target is
required; this is not a promise that every provider or destination is reachable.
The native configuration parser retains its own behavior for mixed valid/invalid
entries. Test an intended destination explicitly before relying on it.

There is no persistent service volume. Provider state is memory-only. Preserve
the ODS environment values, hash/password records and image pins in a private
configuration backup. Recreation rebuilds the same fixed route; temporary caches
and provider state are discarded. Disabling stops delivery and retains the ODS
settings. Restore or rollback uses matching configuration and images; it cannot
replay or undo a message.

Update the gateway hash to rotate its password. Recreate **both** `apprise` and
`apprise-api` so the native process joins the gateway's new network namespace;
Compose's service namespace dependency handles the normal configuration update.
An already accepted delivery may finish with the old configuration. Change
destination credentials in `APPRISE_CONFIG_TEXT` and recreate the API to rotate
them. Revoke old credentials at the destination separately when appropriate.

## Qualification

```bash
python -m pip install pytest bcrypt==5.0.0
python -m pytest ods/tests/test_apprise.py -q
env ODS_TEST_APPRISE=1 python -m pytest ods/tests/test_apprise.py -q -s
```

Linux amd64 native tests use an isolated local HTTP receiver only. They exercise
Unicode JSON delivery, authentication, fixed destinations despite request URL
overrides, downstream failures, disabled redirects/attachments, blocked alternate
APIs, private listener binding, recreation, hash rotation with namespace
reconciliation, invalid configuration rejection and recovery. No external message
is sent. Other provider plugins, SMTP/push services, browser use, load, interrupted
delivery, TLS, ARM/native macOS/Windows and cross-version compatibility remain
unqualified. `/status` proves API readiness, not destination availability.

References: [pinned API contract](https://github.com/caronc/apprise-api/blob/v1.5.4/swagger.yaml),
[native configuration bindings](https://github.com/caronc/apprise-api/blob/v1.5.4/apprise_api/core/settings/__init__.py),
[native notification implementation](https://github.com/caronc/apprise-api/blob/v1.5.4/apprise_api/api/views.py).
