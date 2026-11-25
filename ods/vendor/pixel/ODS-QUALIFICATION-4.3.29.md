# ODS-maintained Pixel 4.3.29 candidate

Qualification date: 2026-10-04 (automated evidence only). Public project:
https://github.com/Osmantic/ODS. Functional ODS source checkpoint:
`50762d145adf245b4c32bdf0b40f5eee8359a510`.

This is an ODS-maintained candidate assembled from the visible public ODS vendor
source. It is not a claim of an upstream/private Pixel release, upstream audit,
signed release envelope, or a completed physical installation qualification.
No private repository or signing credential was used.

## What this release changes

Only workspace template text and version metadata:

- `workspace-template/AGENTS.md`: a retired owner-private operating section is
  replaced by neutral model-routing guidance. The file is byte-identical to the
  output of ODS's existing workspace-guidance migration
  (`installers/lib/pixel-workspace-guidance.py`) applied to the 4.3.28 file, so
  a new workspace starts where a migrated 4.3.28 workspace ends.
- `workspace-template/MEMORY.md`: the matching retired entry is removed, exactly
  as that migration removes it.
- Version metadata from 4.3.28 to 4.3.29: `RELEASE-MANIFEST.json`, the release
  manifest and legacy clean-migration schema constants, the release-update and
  release-contract version allow-lists, the restore target-version gate and the
  four legacy clean-migration test fixtures that pin it, this compatibility row,
  and the 21 files `scripts/generate-release-files.mjs --write` derives from the
  manifest.

OpenClaw 2026.6.33, its official plugin versions, Node, dependency versions,
container image digests and trust anchors are unchanged. No runtime code changed.

The version increment lets the existing strictly increasing source-upgrade
contract install this source over 4.3.28; an installation refuses different
source at the version it already runs. The template change does not rewrite
existing workspaces, because apply copies template files only where a workspace
lacks them. ODS's installer migration keeps replacing the exact retired bytes in
workspaces created from older releases and keeps the owner's originals in a
private backup outside the workspace.

## Automated evidence

Run on 2026-10-04 on a Windows development machine (Node 24.14.1, Python
3.12.10, pytest 8.4.2, Git 2.53). POSIX-only and Linux-only cases cannot run
there and are reported as skipped or not run.

- `node scripts/generate-release-files.mjs --check` passes. `--write` changed
  exactly the 21 version-derived files.
- `node --test`: `tests/legacy-clean-migration-schema.test.mjs` 4 passed,
  `tests/json-schema.test.mjs` 29 passed, `tests/client-kit.test.mjs` 3 passed
  and 1 skipped (symlinks are unavailable on Windows). The 4.3.28 source gives
  the same counts on the same machine.
- ODS `tests/test-seeded-content-privacy.py` passes for 35 seeded files. It now
  fails if the shipped template would need the migration again.
- ODS `pixel-agent` bootstrap filter tests: 22 passed. One reads the exact
  4.3.28 `AGENTS.md` from Git history and confirms the filter presents it as
  the 4.3.29 default.
- ODS `tests/test_pixel_workspace_guidance.py`: 26 passed, using the exact
  4.3.28 files from Git history and a synthetic retired block. 45 cases that need
  a non-root POSIX workspace owner were skipped, and 5 cases that run the bash
  installer helpers were not run.
- ODS `tests/test_pixel_source_upgrade.py`, new version-gate test: 8 passed.
  A changed source at the active version, a downgrade, a pre-release suffix, a
  missing `VERSION` and a marker without an active release version are refused.
  This module imports `fcntl`, so it ran with a stub module on Windows; it runs
  unmodified in Linux CI.

`scripts/check-release-contract.mjs` still fails at its first check on this
public export ("Codex 0.147 comparison prompt differs from the exact reviewed
source"), exactly as on 4.3.28. No audit document or signature is fabricated to
change that. Pixel's `tests/e2e.sh` runs in ODS CI (Vendored Pixel suite);
`tests/static.sh` does not. Neither ran on this machine.

Bundle identity and packaging verification are recorded outside this bundle, in
ODS's `vendor/PIXEL-SOURCE-PROVENANCE.md` and installer pins, to avoid a
self-referential hash.

## Pending qualification

Status remains `candidate`. The following has not been performed and is not
claimed:

- the held source upgrade from 4.3.28 to 4.3.29 on real Linux/WSL
  installations, both in Sandbox mode and with Full Access enabled;
- a macOS native update from 4.3.28 to 4.3.29.

Each must show the new release active with its source and version recorded, the
previous release retained, the access mode preserved and freshly proven before
admission is released, and workspace guidance either already migrated or
migrated with a private backup, with no retired text left in the agent's
bootstrap files.
