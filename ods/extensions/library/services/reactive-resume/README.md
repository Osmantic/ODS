# Reactive Resume

Native MIT resume builder with templates, structured sections, imports and export. Packages official **v5.3.1**, a dedicated PostgreSQL 17 instance and persistent local upload storage. It does not submit applications or publish a resume for the owner.

## Setup

Provide `REACTIVE_RESUME_DB_PASSWORD` and `REACTIVE_RESUME_AUTH_SECRET`, each 64 random hexadecimal characters. The preloader validates them before the original application entrypoint runs. Native startup checks and database migrations remain responsible for application initialization.

Open `http://localhost:11093` and register the intended account through the application. Set `REACTIVE_RESUME_DISABLE_SIGNUPS=true` afterward to close new registration. No preset user/password is created. Use the localhost origin consistently; changing the published port updates `APP_URL` as well. A different hostname or reverse proxy requires explicitly updating the canonical origin and auth configuration.

SMTP and social providers are not configured. Upstream's fallback logs emails instead of delivering them: verification/reset messages may contain sensitive links in container logs. This recipe does not claim working email delivery or bypass verification. Configure an actual mail transport before requiring email workflows for other users. Keep host publishing local until account and access setup is complete.

## Project and model use

Import content through the application's supported formats and download exports explicitly into the intended project. Browser-side PDF generation in this version removes the older Browserless/Chromium service dependency. Actual PDF/font fidelity still requires browser verification. Importing content, exposing a public resume URL or submitting an application is not performed by installation.

No AI provider, saved API credential, Redis service or Portal project association is created. Optional AI features need deliberate provider/encryption configuration; the AI Agent workspace additionally requires Redis-compatible infrastructure. This integration does not pin or change the selected ODS model or context size. It does not weaken upstream private-network provider protections automatically.

## Persistence and operations

`reactive-resume-db-data` stores accounts, resume content and other database records. `reactive-resume-data` stores uploads at `/app/data`. Back up both consistently and retain the authentication secret. Changing the database password in environment does not rotate an existing PostgreSQL account: coordinate that change through database administration.

The app runs as UID/GID 1000 with a read-only base and writable data/tmp paths, two CPUs and 2 GiB RAM. PostgreSQL is on an internal network with no host port and a 1 GiB memory bound. The app health endpoint checks both database and storage; a healthy response is not proof of successful login or export.

Official application and PostgreSQL images are digest-pinned. Application variants cover Linux amd64/arm64; Docker on Windows/macOS must run Linux containers. No host-specific folder mounts or GPU dependencies are used. The repository is undergoing namespace migration; this recipe retains the verified Docker Hub v-prefixed tag rather than assuming a new registry has all historical images.

Build/startup, migrations, account flows, uploads, PDF export, backup/restore and actual platform execution remain runtime-pending. Configuration checks are not end-to-end validation. No services or inference were started.

Sources: [versioned source](https://github.com/AmruthPillai/Reactive-Resume/tree/v5.3.1), [self-hosting guide](https://docs.rxresu.me/self-hosting/docker).
