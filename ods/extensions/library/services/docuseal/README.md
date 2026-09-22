# DocuSeal

Prepare PDF forms with text, date, checkbox and signature fields, complete signing flows and retain the resulting documents. This adds document preparation/signing to ODS; it is not another PDF conversion service.

## Edition and packaging

Official 3.2.5 application image pinned by digest. Upstream licensing is AGPLv3 with section 7(b) terms requiring retention of DocuSeal attribution in interactive interfaces. The recipe preserves upstream branding and notices; no Pro activation or commercial features are promised.

The original Puma command, application migrations and local Redis worker lifecycle are retained. SQLite, attachments and local Redis state use the `docuseal-data` volume at `/data/docuseal`, prepared for upstream UID/GID 2000. Redis is internal on loopback port 16379 and is not published.

Set `DOCUSEAL_SECRET_KEY_BASE` to 64 random bytes encoded as 128 hex characters. Retain this value with backups: it participates in session and application encryption, so replacing it is not an ordinary password change. No fixed credentials or signing recipients are seeded.

## First use and project workflow

Open `http://localhost:11084` (or the configured `DOCUSEAL_PORT`) and complete the upstream administrator setup. Upload the intended PDF, place form fields, and inspect the signing flow before sending invitations. Download the finished document and associated audit material for the relevant Playground project. Installation does not grant Portal access to documents, configure API credentials, send invitations or sign on anyone's behalf.

SMTP is not configured by this recipe; email delivery requires setup through DocuSeal. Public signing links require an externally reachable HTTPS deployment with matching application URL, proxy and mail settings. The default loopback HTTP deployment is local, so a localhost link cannot be used by a recipient on another computer. No DNS, certificate issuance or network exposure is changed automatically.

Keep the full volume and application secret together when backing up, preferably with the service stopped. Documents, account data and queued work are not recreated by downloading the image. Allow the configured 60-second shutdown grace period.

## Scope and platform status

The app has a 2 GiB memory limit, two CPUs, five web threads and two worker threads. PDF processing and the bundled field-detection model use the application runtime; this recipe neither starts ODS inference nor changes the selected chat model. No server GPU is required.

The image publishes Linux amd64/arm64 variants for Docker on Windows, Linux and macOS. Image build, first administrator setup, PDF rendering, signature flow, worker persistence and backup restoration remain runtime-pending. A healthy `/up` response only establishes web application readiness, not delivery or document correctness. This recipe makes no claim about a signature's legal effect.

Upstream: https://github.com/docusealco/docuseal/tree/3.2.5
Attribution terms: https://github.com/docusealco/docuseal/blob/3.2.5/LICENSE_ADDITIONAL_TERMS
