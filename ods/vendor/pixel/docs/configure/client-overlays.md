---
title: Configure Pixel client overlays
doc_type: how-to
audience: [owner, contributor, maintainer]
feature_status: supported
owners: [documentation, configuration]
sources_of_truth: [CLIENT-KIT.md, client-overlay.example.json, schemas/client-overlay-v1.schema.json, scripts/client-kit.mjs]
last_verified_at: 2026-08-27
---

# Configure Pixel client overlays

A client overlay records a private, reviewable contract around the immutable Pixel core: client identity, profile, Frontier/operator access, privacy statements, extensions, licensing, and usability evidence. It is not a credential store and does not modify core authority.

## Generate and review

Start from `client-overlay.example.json` or the exact `./pixel client-kit` workflow described in [CLIENT-KIT.md](../../CLIENT-KIT.md). Keep the result private and validate it against `schemas/client-overlay-v1.schema.json`.

Review:

- the exact core/release binding;
- selected capability profile and deployment boundary;
- declared extensions and their signed evidence;
- licensing evidence and its real legal status;
- usability claims and exact supporting evidence;
- privacy and operator-access statements.

Schema validity proves structure and constants, not the legal sufficiency of an agreement, accuracy of a usability claim, deployment, support, or acceptance.

## Change and rollback

Create a new reviewed overlay rather than silently overwriting historical evidence. Revalidate every referenced extension and core identity. Overlay removal does not uninstall extensions, revoke credentials, or roll back runtime state; use each owning lifecycle.
