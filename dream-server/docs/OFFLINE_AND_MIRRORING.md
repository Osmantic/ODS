# Offline And Mirroring Guide

Dream Server should be usable as independently owned infrastructure. This guide
explains how operators and forks can reduce reliance on mutable upstream state by
pinning, mirroring, and recording release artifacts.

This is not a promise that every upstream service, model, or image license
permits redistribution. Mirror only what you are allowed to mirror.

## What To Preserve

For a durable downstream release, preserve:

- the Dream Server git ref;
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
git clone --mirror https://github.com/Light-Heart-Labs/DreamServer.git
cd DreamServer.git
git remote set-url --push origin <your-mirror-url>
git push --mirror
```

For a working fork, pin your release in `DOWNSTREAM.md`:

```text
Upstream: Light-Heart-Labs/DreamServer
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
Dream Server ref:
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

## Recovery If Upstream Disappears

If upstream is unavailable:

1. Use your mirrored git ref.
2. Restore mirrored Docker images or retag local images.
3. Restore mirrored model files and checksums.
4. Use pinned installer commands or local install scripts.
5. Run the validation subset from [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md).
6. Record a new local validation receipt.

The goal is not to freeze Dream Server forever. The goal is to make each release
understandable and recoverable without depending on mutable external state.
