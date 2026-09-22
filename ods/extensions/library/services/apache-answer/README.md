# Apache Answer

A local question-and-answer knowledge base, with tags, accepted answers,
moderation and user accounts. Licensed under Apache-2.0.

## Installation and setup

Install Apache Answer from ODS Extensions. Open its service link (default
`http://localhost:11147`), choose SQLite in the initial wizard, then configure
the site URL and create your administrator account. ODS does not preset an
administrator password. Keep the loopback binding until setup is complete.
HTTP readiness means the application is reachable, not that onboarding has
been completed. Optional email configuration is managed in Answer's settings.

The `apache-answer-data` Docker volume holds configuration, the SQLite database,
uploads and translations. Disabling the extension preserves this volume.
Back up the volume while the service is stopped before upgrading; the upstream
entrypoint performs database upgrades when a new version starts.

`APACHE_ANSWER_PORT` changes the published port. Do not substitute an external
database without updating and validating the application's own configuration.
This recipe deliberately uses the supported local SQLite setup.

## Platform and verification

The pinned official image includes Linux amd64 and arm64 variants. It needs no
GPU or loaded LLM. Docker Engine on Linux and Docker Desktop on Windows/macOS
provide the container runtime. No x86 emulation is required on Apple Silicon.

The image index, Compose configuration and ODS staging checks are verified.
Application startup, account creation and OS-specific runtime behavior have not
been tested here. No container or model was started for this integration.

- [Official installation](https://answer.apache.org/docs/installation/)
- [Release source](https://github.com/apache/answer/tree/v2.0.2)
- [License](https://github.com/apache/answer/blob/v2.0.2/LICENSE)
- Image digest and platform evidence: `upstream.json`.
