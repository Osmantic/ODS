# LimeSurvey Community Edition

Create branching questionnaires, manage participant access, collect research
responses and export results. GPL-2.0-or-later LimeSurvey 7.1.1+260914, using the
digest-pinned **community-maintained** martialblog Apache image (MIT image recipe).
This is not an official LimeSurvey Docker image or the hosted commercial service.

## Setup

Provide distinct random 64-hex `LIMESURVEY_DB_PASSWORD` and
`LIMESURVEY_ADMIN_PASSWORD`, and the owner's actual `LIMESURVEY_ADMIN_EMAIL`.
Open `http://localhost:11125/index.php/admin`; the initial administrator is `ods`.
The upstream installer initializes PostgreSQL and the account. Existing accounts
are not reset when the initial credentials change. Rotate account passwords in
the application; an environment change alone does not rotate PostgreSQL either.

No surveys, participant lists, SMTP configuration or invitations are created.
Create the actual survey, review its privacy/access settings and activate it when
ready to collect responses. Email invitations require configured SMTP and actual
owner-selected recipients. The local URL is reachable only on this computer;
remote respondents require an explicitly configured HTTPS ingress and correct
canonical survey URL. Installing does not publish a survey to the internet.

## Persistence and upgrades

Three named volumes preserve PostgreSQL data, `/var/www/html/upload` (including
uploaded themes/assets) and `/var/lib/ods-limesurvey` (encryption configuration).
Docker initializes the upload volume from the packaged directory. No host path
or manual copying of the initial upload tree is required.

The native `application/config/security.php` points into the private encryption
volume. LimeSurvey generates its own keys on first use, then retains them across
container recreation. Back up that volume **together with the database and
uploads**; encrypted responses cannot be recovered from the database alone.
Do not replace keys during reinstall. This integration preserves the application's
native encryption design; it does not enable encryption on every survey field.

Native startup checks/upgrades the database schema automatically. Take a database
dump and backups before upgrading, and follow the upstream upgrade instructions.
Do not change PostgreSQL major versions in place. Application config/cache remains
writable in the container for the upstream startup; the whole application source
is not frozen inside a persistent volume. Install custom code through a revised
recipe rather than assuming container-layer changes survive recreation.

## Operation

Native www-data user and Apache startup, private PostgreSQL 17 with no published
database port, loopback HTTP 11125. Remote-IP header rewriting is disabled for
direct local access. Configure a known proxy explicitly if deploying behind one.
App limits: 2 GiB/two CPUs; database: 1 GiB. HTTP health only confirms the admin
endpoint responds, not survey logic, account access or delivery of invitations.

Linux amd64/arm64 images support the Linux-container path on Docker Engine and
Windows/macOS Docker Desktop. No GPU/model dependency or host bind path is used.
Image build, first installation/login, submissions, encryption persistence and
restore across platforms remain runtime validation pending. No services/models
were started while preparing this recipe.

Sources: [LimeSurvey source](https://github.com/LimeSurvey/LimeSurvey/tree/7.1.1%2B260914),
[image source](https://github.com/martialblog/docker-limesurvey/tree/f4c023e9cb08a7a55ca6f39a5f1e2f5d6910050f),
[data encryption](https://www.limesurvey.org/manual/Data_encryption).
