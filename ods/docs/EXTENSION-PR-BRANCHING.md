# Extension changes: branch targets

ODS keeps both **core runtime** (installer, `ods/` compose, CLI, shipped extensions under `ods/extensions/services/`) and the optional **extensions library** under `ods/extensions/library/`.

Use this guide when coordinating PRs that touch extensions or integrations.

## Quick rules

| Change | Target branch | Notes |
|--------|---------------|--------|
| Installer, `ods-cli`, compose base files, dashboard, dashboard-api, **shipped** `ods/extensions/services/*` used by default installs | **main** (via normal PR flow) | Follow [EXTENSIONS.md](EXTENSIONS.md) for manifest/schema. |
| Catalog-only updates: new or updated entries under **`ods/extensions/library/`** (extra services, workflows, templates) | **main** (via normal PR flow) | Regenerate `ods/config/extensions-catalog.json` when manifests change. |
| Docs-only (troubleshooting, field reports) | **main** | Unless your team batches docs on a doc branch. |
| **Both** core behavior and catalog | Split PRs or one PR with explicit maintainer agreement | Easier review when core and catalog are separate. |

## Linux / Windows parity

Platform-specific installer scripts (`installers/`, `ods/installers/windows/`) usually land on **main** with tests. Cross-platform doc additions (e.g. [LINUX-TROUBLESHOOTING-GUIDE.md](LINUX-TROUBLESHOOTING-GUIDE.md)) should stay aligned with the same check IDs and behavior as the scripts they reference.

## Questions?

- Default extensions and schema: [EXTENSIONS.md](EXTENSIONS.md)
- Installer layout: [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md)
