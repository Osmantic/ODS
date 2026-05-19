# Extension changes: branch targets

Dream Server keeps both **core runtime** (installer, `dream-server/` compose, CLI, shipped extensions under `dream-server/extensions/services/`) and the optional **extensions library** under `dream-server/extensions/library/`.

Use this guide when coordinating PRs that touch extensions or integrations.

## Quick rules

| Change | Target branch | Notes |
|--------|---------------|--------|
| Installer, `dream-cli`, compose base files, dashboard, dashboard-api, **shipped** `dream-server/extensions/services/*` used by default installs | **main** (via normal PR flow) | Follow [EXTENSIONS.md](EXTENSIONS.md) for manifest/schema. |
| Catalog-only updates: new or updated entries under **`dream-server/extensions/library/`** (extra services, workflows, templates) | **main** (via normal PR flow) | Regenerate `dream-server/config/extensions-catalog.json` when manifests change. |
| Docs-only (troubleshooting, field reports) | **main** | Unless your team batches docs on a doc branch. |
| **Both** core behavior and catalog | Split PRs or one PR with explicit maintainer agreement | Easier review when core and catalog are separate. |

## Linux / Windows parity

Platform-specific installer scripts (`installers/`, `dream-server/installers/windows/`) usually land on **main** with tests. Cross-platform doc additions (e.g. [LINUX-TROUBLESHOOTING-GUIDE.md](LINUX-TROUBLESHOOTING-GUIDE.md)) should stay aligned with the same check IDs and behavior as the scripts they reference.

## Questions?

- Default extensions and schema: [EXTENSIONS.md](EXTENSIONS.md)
- Installer layout: [INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md)
