# Garage: local object artifacts

Garage 2.3.0 provides a native S3 endpoint for reproducible local artifacts.
This recipe deliberately has **one node and one copy**. It offers no redundancy
against a lost disk or host and must not be the sole copy of irreplaceable data.
Use the upstream multi-node deployment design for a redundant storage service.

## Enable and connect

From `ods/` on Linux, prepare private storage:

```bash
sudo install -d -m 0700 -o 1000 -g 1000 data/garage
```

In Extensions, supply four distinct values before enabling Garage:

- `GARAGE_RPC_SECRET`: 64 random hexadecimal characters; preserve with backups.
- `GARAGE_ADMIN_TOKEN`: a long random administration token.
- `GARAGE_ACCESS_KEY`: `GK` followed by 32 random hexadecimal characters.
- `GARAGE_SECRET_KEY`: 64 random hexadecimal characters for that access-key ID.

The native `--single-node --default-bucket` startup creates layout version 1
and bucket `ods-artifacts`. The bootstrap key owns that bucket and can create
additional buckets. Use native administration to issue narrower application keys;
the bootstrap key is an operator credential.

Configure S3 clients with region `garage`, **path-style addressing**, endpoint
`http://localhost:3900` and an application access-key pair. Trusted ODS containers
use `http://garage:3900`. `GARAGE_S3_PORT` changes the host port; `BIND_ADDRESS`
defaults to loopback. This is an S3 API, with no browser console or public website.
The pinned boto3 regression uses SigV4 and checksum calculation `when_required`;
arbitrary AWS features and client defaults are not all supported by Garage.

## Administration and authentication

Only S3 port 3900 is published. Native administration listens on 3903 inside
`ods-network` and requires `Authorization: Bearer <GARAGE_ADMIN_TOKEN>` for
management operations. Native `/health` is public readiness information, not an
authorization check. Local RPC binds loopback 3901 and requires the RPC secret.
Keep ODS peers trusted: this local profile uses HTTP, not TLS transport security.
No S3 website or K2V endpoint is configured.

Use the pinned native `/v2/CreateKey`, `/v2/GetBucketInfo`, `/v2/AllowBucketKey`
and `/v2/DeleteKey` APIs, or `/garage` CLI inside the container, to manage keys
and bucket grants. A read-only key can retrieve objects and issue presigned reads
but cannot upload or delete them. Presigned URLs are temporary bearer access to
their object; handle them as credentials.

Bootstrap behavior is deliberately different from a one-time password seed:

- The current bootstrap key's read/write/owner permissions are reapplied at
  every startup. Use additional native keys for restricted workflow access.
- An existing bootstrap key ID with a different secret **stops startup**.
  Restore the matching value to recover; editing the secret in place is not key
  rotation. A previously deleted key ID cannot be imported again.
- To rotate bootstrap credentials, configure a **new ID and new secret**,
  recreate Garage, verify access, then explicitly revoke the old key. Do not
  delete the currently configured bootstrap key. Rotate the administration token
  by changing its value and recreating; old bearer tokens then stop working.
- `--single-node` rejects a multi-node layout. Expanding into a cluster requires
  an explicit deployment change, not merely adding a peer to this recipe.

The scratch image runs UID/GID 1000 with a read-only root, dropped capabilities,
32 MiB temporary storage and limits of 1 CPU / 512 MiB RAM. Native `/garage status`
is the container health probe because the image contains no shell/HTTP client;
it establishes daemon/RPC availability. Dashboard `/health` and successful S3
requests provide separate readiness and application evidence.

## State, recovery and rollback

Keep all of `data/garage`, including SQLite metadata, layout/node identity and
object blocks. Stop Garage gracefully before taking a private cold copy together
with its four configuration values. Restore into a separate empty directory with
UID/GID 1000 ownership and the matching image. Verify the layout, bucket grants,
object contents and metadata, and both positive and denied client operations.
Copying only blocks or only SQLite metadata is not a complete backup. A successful
PUT or multipart response is not evidence of survival after physical disk loss.

Disable retains the data. Before an upgrade, take a cold backup; rollback uses
that backup with its matching image and credentials. Storage growth has no recipe
disk quota. Operators must monitor capacity and explicitly manage artifact
retention. Object removal does not promise immediate physical block reclamation.

## Qualification

```bash
python -m pip install pytest boto3==1.40.40
python -m pytest ods/tests/test_garage.py -q
sudo --preserve-env=PATH env ODS_TEST_GARAGE=1 python -m pytest ods/tests/test_garage.py -q -s
```

Native Linux amd64 tests cover automatic layout/bucket initialization, rejected
admin and S3 credentials, Unicode bytes and metadata, range/presigned reads,
6 MiB multipart round trips, read-only bucket permissions, recreation, deliberate
bootstrap mismatch and safe recovery, explicit key/token rotation, complete cold
restore, revocation and object deletion. The fixture publishes administration
only on an isolated ephemeral loopback port; the product recipe does not.
Multi-node recovery, disk loss, power-cut durability, load, ARM/native macOS/Windows
runtime, TLS termination and cross-version migration remain unqualified.

References: [single-node quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/),
[configuration](https://garagehq.deuxfleurs.fr/documentation/reference-manual/configuration/),
[pinned source](https://github.com/deuxfleurs-org/garage/tree/v2.3.0).
