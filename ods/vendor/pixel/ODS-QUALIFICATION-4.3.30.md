# ODS-maintained Pixel 4.3.30 candidate

Functional source checkpoint: `81b278d3be292dac9226f3f0815bb329a66d74ee` in
the public ODS repository. This is an ODS-maintained candidate, not an upstream
Pixel release or a completed installation qualification.

## Change

An actual clean WSL installation stopped while applying Operations reader ACLs
because the broker had not published its inventory. The service was active,
and a later observation found a regular inventory file. Its periodic atomic
replacement prevents inferring the first publication time from a later stat.

The old installer waited forty quarter-second polling intervals, then checked
only service activity before applying ACLs. The candidate waits for both service
activity and inventory publication, with a sixty-second elapsed-time deadline.
A failed service stops the wait early. The existing file, symlink, reader-access
and private-state checks still run and are not relaxed.

Version metadata advances from 4.3.29 to 4.3.30 so the existing source-upgrade
contract can distinguish changed source. OpenClaw, Node, dependencies, image
digests, policies and trust anchors are unchanged. The version-derived files
come from `scripts/generate-release-files.mjs`.

## Current evidence

- Six Linux tests passed for actual delayed atomic-file publication, immediate
  publication, failed and inactive services, missing inventory, and symlink
  rejection. Their sudo and systemd boundaries are fixtures; they do not claim
  live service acceptance.
- The delayed case publishes after twelve seconds, outside the old nominal
  ten-second wait window.
- Generated release files match the release manifest.

## Pending qualification

Keep status `candidate` until the packaged source passes its exact-head checks
and a controlled live delayed-start test. Clean Linux/WSL installation, the
held source upgrade with access-mode custody, and the macOS native path must
be verified separately before claiming fleet readiness. Existing failed-install
receipts remain failures even when a later broker starts successfully.

The outer ODS provenance document records the synthetic bundle identity and
packaging verification. No private source history or signing credential is used.
