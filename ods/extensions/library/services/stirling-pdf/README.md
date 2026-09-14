# Stirling-PDF

Run PDF tools on the ODS host with Stirling-PDF 2.14.3. The recipe pins the
multi-architecture image and enables local account authentication. Core PDF
operations use the server's CPU; no model service or GPU is required. Some
upstream features have separate licensing requirements.

## Install and use

Set a unique `STIRLING_PDF_ADMIN_PASSWORD` in ODS's `.env` before installing
from the dashboard's Extensions library. Alternatively, copy this entire
directory to `extensions/services/stirling-pdf`, set the password in `.env`,
then run `ods enable stirling-pdf`. The library details list required environment
keys; they do not collect credentials in a setup form. Set
`STIRLING_PDF_ADMIN_USER` before first startup to use a username other than `admin`.

Open `http://localhost:7823` and sign in. Set `STIRLING_PDF_PORT` before enabling
if that port is occupied. Container HTTP remains on 8080. Login credentials
initialize a new database only; changing `.env` later does not reset an existing
account's password. Use the application's account settings for password changes.

The image starts as root for its documented directory initialization, then runs
the application as UID/GID 1000. ODS prepares writable bind directories using
the manifest UID. Custom/rootless installations need equivalent UID mappings.
Allow 2 GiB of memory and space for the full image, temporary conversions and
your stored files. Cold initialization includes font permissions and office
tools before Java starts; allow up to ten minutes on a busy host. The configured
upload limit is 100 MB.

## Local data and access

Authentication is enabled, and the published port binds to loopback by default.
Remote use needs the protected ODS access path with HTTPS. Analytics, PostHog,
Scarf, update notices and surveys are disabled in the recipe. This does not
disable tools where an operator explicitly requests a remote URL or account;
review those operations separately before submitting sensitive documents.

Application data is under `data/stirling-pdf/`: `configs` contains account and
settings data, `storage` saved files, `pipeline` automation definitions,
`customFiles` custom assets, `tessdata` added OCR languages, and `logs` logs.
Conversions also use temporary container storage. Download resulting files to
keep them independently of the application. Disabling the extension preserves
host data; it does not delete uploaded or saved documents.

Stop the service before backing up the entire directory, especially the account
database in `configs`. Before upgrading, retain that backup and the old image
digest. For rollback, restore the matching stopped-service backup and image;
do not assume a newer account database can be opened by an older version.

## Validation

The opt-in Docker test exercises the installed Compose recipe, requires initial
credentials, checks authentication, rotates a generated PDF through the real
API, and verifies the account still works after container recreation. Normal
tests check generated catalog discovery and the declared configuration.

```bash
python -m pip install pytest httpx PyYAML pypdf
ODS_TEST_STIRLING_PDF_DOCKER=1 pytest -q tests/test_stirling_pdf_extension.py
```

The live target is Linux amd64 Docker on WSL. ARM images are published upstream,
but native macOS/Windows, ARM execution, OCR, office conversion, browser editing,
and every other upstream tool require separate validation.

Sources: [2.14.3 release](https://github.com/Stirling-Tools/Stirling-PDF/releases/tag/v2.14.3),
[Docker installation](https://docs.stirlingpdf.com/Installation/Docker%20Install/),
[configuration](https://docs.stirlingpdf.com/Configuration/).
