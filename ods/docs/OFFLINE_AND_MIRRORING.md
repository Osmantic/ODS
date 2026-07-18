# Offline And Mirroring Guide

ODS should be usable as independently owned infrastructure. This guide
explains how operators and forks can run from their own pinned refs, mirrors, and
release receipts instead of depending on mutable upstream state at install time.

This is not a promise that every upstream service, model, or image license
permits redistribution. Mirror only what you are allowed to mirror.

For the difference between `main`, tagged releases, pinned commits, and
downstream fork channels, start with
[RELEASE_CHANNELS.md](RELEASE_CHANNELS.md).

## What To Preserve

For a durable downstream release, preserve:

- the ODS git ref;
- release notes and validation receipt;
- Docker image references and digests where available;
- model filenames, URLs, checksums, and licenses;
- extension manifests and compose fragments;
- installer command and flags;
- generated `.env.example` defaults for the edition;
- hardware and driver assumptions.

## Git Mirroring

For an internal mirror:

```bash
git clone --mirror https://github.com/Osmantic/ODS.git
cd ODS.git
git remote set-url --push origin <your-mirror-url>
git push --mirror
```

For a working fork, pin your release in `DOWNSTREAM.md`:

```text
Upstream: Osmantic/ODS
Upstream ref: <commit-or-tag>
Downstream ref: <commit-or-tag>
Validation receipt: <date-and-run-id-or-local-report>
```

## Docker Images

Where licensing permits, mirror images needed by your selected service set:

```bash
docker pull <image>:<tag>
docker tag <image>:<tag> <your-registry>/<image>:<tag>
docker push <your-registry>/<image>:<tag>
```

Prefer digest-pinned records for release receipts. If a service still uses a tag
pin, record the digest resolved during validation.

On high-latency or unreliable links, the Linux installer gives transient Docker
pull failures four attempts total, with `5 15 30` second waits between retries.
Operators can tune this without editing the installer:

```bash
ODS_DOCKER_PULL_MAX_ATTEMPTS=4 ODS_DOCKER_PULL_RETRY_DELAYS="10 30 60" ./install.sh
```

## Models

For model mirrors:

- record source URL;
- record filename;
- record SHA256 or provider checksum;
- record license and redistribution terms;
- keep partial downloads out of the final mirror;
- test that the installer or model swap path can use the mirrored location.

If a model cannot be redistributed, document the required download source and
checksum so operators can reproduce the artifact themselves.

## Extension Assets

Custom extensions should keep assets near the extension when practical:

```text
extensions/services/<service-id>/
  manifest.yaml
  compose.yaml
  assets/
  README.md
```

For large assets, store checksums and retrieval instructions in the extension
README.

## Offline Release Receipt

Keep a receipt with every offline-capable image or appliance:

```text
ODS ref:
Downstream ref:
Install mode:
Hardware class:
Docker images mirrored:
Models mirrored:
Services enabled:
Validation commands:
Known skipped surfaces:
Operator notes:
```

The receipt is what lets another maintainer rebuild trust without access to the
original lab.

## Operating From Your Own Mirror

When you choose to operate from a mirror:

1. Use your mirrored git ref.
2. Restore mirrored Docker images or retag local images.
3. Restore mirrored model files and checksums.
4. Use pinned installer commands or local install scripts.
5. Run the validation subset from [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md).
6. Record a new local validation receipt.

The goal is not to freeze ODS at one point in time. The goal is to make
each release inspectable and reproducible from artifacts an operator controls.
