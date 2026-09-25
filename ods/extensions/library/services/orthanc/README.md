# Orthanc for ODS

Local DICOM archive using the official Orthanc Team image pinned by digest.
Orthanc is GPL-3.0-or-later; the enabled viewer/plugins include AGPL-3.0-or-later
components. Sources and licensing: https://orthanc.uclouvain.be/book/faq/licensing.html
and https://github.com/orthanc-server/orthanc-builder.

## Setup

Set `ORTHANC_PASSWORD` to an independently generated 64-character lowercase hex
secret, then install `orthanc` from Extensions. Open `http://localhost:11143/`
and sign in as `orthanc`. `ORTHANC_PORT` changes the host port. The password is
applied through native configuration on every restart, not a one-time account
bootstrap; changing it affects existing API clients too.

Orthanc Explorer 2, Stone Web Viewer and DICOMweb are enabled. Import local DICOM
files from the web interface, inspect the archive and launch its viewer. REST
clients use the same authenticated origin; clients in an explicitly authorized
ODS project container use `http://orthanc:8042` on `ods-network`, not localhost.
No credentials are inserted into projects or passed to the loaded AI model.

The DICOM network listener is disabled. No scanner, external PACS, modality,
remote peer or transfer destination is invented. Setting up network DICOM later
requires explicit listener, modality/AE and network access configuration.
This integration is for local development/research; it does not establish
clinical validation, diagnosis capability or compliance for patient records.

## Storage and operation

`orthanc-data` retains the native SQLite index and DICOM objects together under
`/var/lib/orthanc/db`. Stop Orthanc before taking a consistent whole-volume
backup and retain its configuration and secret separately. Preserve the volume
when recreating or upgrading the service. No study data is downloaded at install.
Do not treat deleting a container as deleting its retained studies.

Runs as the image's native UID/GID 999. The filesystem remains writable because
native startup generates host ID, plugin links and configuration; the wrapper
sets private creation permissions and validates the required secret. Lua
execution and REST filesystem export are disabled. HTTP is authenticated and
published on loopback only. Remote exposure requires a separately configured
authenticated TLS deployment.

CPU-only amd64/arm64 Linux image, through Docker Engine on Linux or Docker
Desktop Linux containers on Windows/macOS. Limits: 2 GB RAM, two CPUs; large
studies/viewer work need additional disk and browser memory. No GPU or model
backend is selected or changed.

The native database-aware health probe reads generated credentials and queries
`/changes?last`. ODS uses Docker health rather than an unauthenticated HTTP probe
that would incorrectly reject the login-protected server. This checks archive
availability, not correctness of imaging results or every viewer feature.

Schema/Compose/staging checks are separate from runtime validation. Build,
login, DICOM import/query/viewer, password rotation and backup/restore/platform
checks remain pending. No containers or models were started during integration.
