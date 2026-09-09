# Portal-on-Hermes Predecessor Inventory: Subsystem Summary

**Ledger:** codex-portal-hermes-release-green-01a08297
**Branch:** feat/portal-hermes-agent (e6a3dd36aa28)
**Records:** 427
**Digest:** `7209d91e9dba1d20106babe91193406b090ee72fb80284d9740ca6ecb09c9147`

---

## Subsystem Breakdown

| Subsystem | Records | Dispositions |
|-----------|---------|-------------|
| tests/CI/docs | 199 | Retain semantics: 198, Replace OpenClaw coupling: 1 |
| dashboard/API | 41 | Review required: 24, Retain semantics: 14, Replace OpenClaw coupling: 3 |
| providers/connections | 39 | Retain semantics: 37, Replace OpenClaw coupling: 2 |
| installer/migration/rollback/uninstall | 37 | Orthogonal retain: 37 |
| core/runtime replacement | 26 | Replace OpenClaw coupling: 22, Review required: 4 |
| operations/frontier | 22 | Orthogonal retain: 11, Review required: 10, Replace OpenClaw coupling: 1 |
| model/fleet routing | 19 | Retain semantics: 12, Orthogonal retain: 7 |
| ingress/edge | 11 | Retain semantics: 9, Replace OpenClaw coupling: 2 |
| access/scopes | 10 | Replace OpenClaw coupling: 8, Review required: 2 |
| previews/artifacts/extensions | 10 | Replace OpenClaw coupling: 10 |
| leases/approvals | 9 | Replace OpenClaw coupling: 8, Retain semantics: 1 |
| observability/receipts/custody | 4 | Retain semantics: 2, Replace OpenClaw coupling: 2 |

## Disposition Summary

- **Retain semantics:** 273
- **Replace OpenClaw coupling:** 59
- **Orthogonal retain:** 55
- **Review required:** 40

## Review-Required Records

**40** records marked review-required:

```
  [pr-3385] M ods/.env.example
  [pr-3385] M ods/bin/ods-host-agent.py
  [pr-3385] A ods/bin/pixel_access_bridge.py
  [pr-3385] A ods/bin/pixel_access_client.py
  [pr-3385] M ods/extensions/services/dashboard-api/config.py
  [pr-3385] M ods/extensions/services/dashboard-api/context_policy.py
  [pr-3385] M ods/extensions/services/dashboard-api/helpers.py
  [pr-3385] M ods/extensions/services/dashboard-api/host_agent_client.py
  [pr-3385] M ods/extensions/services/dashboard-api/main.py
  [pr-3385] M ods/extensions/services/dashboard-api/models.py
  [pr-3385] M ods/extensions/services/dashboard-api/performance_oracle.py
  [pr-3385] M ods/extensions/services/dashboard-api/routers/extensions.py
  [pr-3385] M ods/extensions/services/dashboard-api/routers/models.py
  [pr-3385] M ods/extensions/services/dashboard-api/routers/remote_provider_status.py
  [pr-3385] M ods/extensions/services/dashboard/Dockerfile
  [pr-3385] M ods/extensions/services/dashboard/entrypoint.sh
  [pr-3385] M ods/extensions/services/dashboard/nginx.conf
  [pr-3385] M ods/extensions/services/dashboard/package-lock.json
  [pr-3385] M ods/extensions/services/dashboard/package.json
  [pr-3385] M ods/extensions/services/dashboard/src/hooks/useModels.js
  [pr-3385] M ods/extensions/services/dashboard/src/main.jsx
  [pr-3385] M ods/extensions/services/dashboard/src/pages/Models.jsx
  [pr-3385] M ods/extensions/services/dashboard/src/pages/RemoteProvider.jsx
  [pr-3385] M ods/extensions/services/dashboard/src/pages/Settings.jsx
  [pr-3385] M ods/extensions/services/dashboard/src/plugins/core.js
  [pr-3385] A ods/extensions/services/dashboard/src/utils/staleAssetRecovery.js
  [pr-3385] A ods/extensions/services/pixel-agent/README.md
  [pr-3385] A ods/extensions/services/pixel-agent/manifest.yaml
  [pr-3385] M ods/lib/safe-env.sh
  [pr-3385] M ods/lib/service-registry.sh
  [pr-3385] M ods/scripts/audit-extensions.py
  [pr-3385] M ods/scripts/generate-extensions-catalog.py
  [pr-3385] M ods/scripts/render-runtime-configs.py
  [pr-3385] M ods/scripts/repair/repair-perplexica.sh
  [pr-3385] M ods/scripts/resolve-compose-stack.sh
  [pr-3818] M .gitattributes
  [pr-3818] M ods/.env.example
  [pr-3818] M ods/bin/ods-host-agent.py
  [pr-3818] M ods/extensions/services/dashboard-api/main.py
  [pr-3818] M ods/extensions/services/dashboard/src/pages/Settings.jsx
```

## Manual Classifications

All classifications are mechanical (auto-routed). No manual semantic review performed.

---

**Note:** This inventory proves provenance coverage and classification only.
It does not prove implementation, qualification, installed state, or live acceptance.

**Disposition status:** All non-review-required dispositions are mechanical routing hypotheses pending integration review. They have not been verified against actual implementation or semantic equivalence. 40 records require explicit review.
