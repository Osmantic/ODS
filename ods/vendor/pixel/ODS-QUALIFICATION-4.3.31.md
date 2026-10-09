# ODS-maintained Pixel 4.3.31 candidate

Functional source checkpoint: `1ff18ccdc559676944792ce0ef3df31ab91b9997` in public ODS.
This is an ODS-maintained candidate, not an upstream Pixel release or fleet pass.

## Change

A clean Windows/WSL install of ODS `689234300e9731ccc261690e5d8329eb14afd627`
still failed the sixty-second Operations inventory deadline. Real service
experiments measured 88.85 seconds before a trivial Python command started
under the installed isolation settings, versus 0.06 seconds without them.
The filesystem-isolation group took 87.05 seconds; the remaining protections
took 0.04 seconds. Policy validation and projection took about 0.006 seconds.
These measurements isolate pre-exec setup; they do not diagnose a kernel cause.

Use `Type=exec` so `systemctl start` waits for successful executable startup.
Give that phase a bounded 180-second timeout. The existing sixty-second
inventory wait follows executable startup and still requires an active service
and a regular projection before applying the existing ACL/private-state checks.
No filesystem, identity, syscall, capability or credential protection changes.

Version 4.3.31 distinguishes this source under the existing strict upgrade gate.
Runtime and dependency versions, image pins, policies and trust anchors stay fixed.

## Qualification

The generated-service contracts and a real WSL startup with unchanged sandbox
settings are required. Record their results in the ODS pull request. Synthetic
unit checks are not clean installation acceptance. Repeat the current-main
clean install and user journeys on all four required hosts after merge.

The previous failed installation and 4.3.30 qualification remain historical
evidence. The outer provenance file records reproducible public-only bundling.
