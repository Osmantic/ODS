---
title: Services, paths, and ports
doc_type: reference
audience: [owner, operator, contributor, security-reviewer]
feature_status: mixed
owners: [documentation, operations, security]
sources_of_truth: [.env.example]
generated_by: scripts/docs/generate-platform-reference.mjs
last_verified_at: generated
---

<!-- GENERATED FILE. Run `node scripts/docs/generate.mjs --write`; do not edit by hand. -->

# Services, paths, and ports

These are sanitized repository example values, not proof of the paths, ports, units, enabled features, users, groups, or listeners on an installed host. Configuration can override them. Use `./pixel verify`, effective service properties, and the private generated environment to establish host truth without copying secret content.

## Gateway and owner deployment

| Configuration key | Repository example value | Evidence/privacy boundary |
|---|---|---|
| `OPENCLAW_HOME` | `/home/agent/.openclaw` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_INSTALL_DIR` | `/home/agent/.local/share/pixel` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_SYSTEMD_UNIT` | `openclaw-gateway.service` | Service identity; verify the effective installed unit |
| `PIXEL_GATEWAY_PORT` | `18789` | Configured listener input; bind and network policy decide exposure |
| `PIXEL_WORKSPACE` | `/home/agent/.openclaw/workspace-pixel` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_PRIVATE_ONBOARDING_PATH` | `/home/agent/.config/pixel-deployment/onboarding.json` | Private custody; do not copy or inspect contents for routine diagnosis |

## Web Courier

| Configuration key | Repository example value | Evidence/privacy boundary |
|---|---|---|
| `PIXEL_WEB_COURIER_UNIT` | `pixel-web-courier.service` | Service identity; verify the effective installed unit |
| `PIXEL_COURIER_SYSTEMD_DIR` | `/etc/systemd/system` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_WEB_COURIER_LOG_PATH` | `/home/agent/.openclaw/logs/web-courier.jsonl` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_WEB_COURIER_BROWSER_PATH` | `/home/agent/.local/share/pixel/browsers` | Generated/configured path; verify the installed host instead of assuming the example value |

## Reference model

| Configuration key | Repository example value | Evidence/privacy boundary |
|---|---|---|
| `PIXEL_REFERENCE_MODEL_PORT` | `8000` | Configured listener input; bind and network policy decide exposure |

## Google source custody

| Configuration key | Repository example value | Evidence/privacy boundary |
|---|---|---|
| `PIXEL_GOOGLE_CONFIG_DIR` | `/home/agent/.config/pixel-google-workspace` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_GOOGLE_TOKEN_PATH` | `/home/agent/.config/pixel-google-workspace/token.json` | Private custody; do not copy or inspect contents for routine diagnosis |

## Source Broker

| Configuration key | Repository example value | Evidence/privacy boundary |
|---|---|---|
| `PIXEL_SOURCE_BROKER_UNIT` | `pixel-source-broker.service` | Service identity; verify the effective installed unit |
| `PIXEL_SOURCE_BROKER_TIMER` | `pixel-source-broker.timer` | Service identity; verify the effective installed unit |
| `PIXEL_SOURCE_ACTION_UNIT` | `pixel-source-action@.service` | Service identity; verify the effective installed unit |
| `PIXEL_SOURCE_RECONCILE_UNIT` | `pixel-source-reconcile@.service` | Service identity; verify the effective installed unit |
| `PIXEL_SOURCE_DIRECT_UNIT` | `pixel-source-direct.service` | Service identity; verify the effective installed unit |
| `PIXEL_SOURCE_DIRECT_PATH_UNIT` | `pixel-source-direct.path` | Service identity; verify the effective installed unit |
| `PIXEL_SOURCE_BROKER_SYSTEMD_DIR` | `/etc/systemd/system` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_SOURCE_BROKER_INSTALL_DIR` | `/opt/pixel-source-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_SOURCE_BROKER_STATE_DIR` | `/var/lib/pixel-source-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_SOURCE_TOKEN_PATH` | `/var/lib/pixel-source-broker/private/google-token.json` | Private custody; do not copy or inspect contents for routine diagnosis |
| `PIXEL_SOURCE_PROJECTION_DIR` | `/var/lib/pixel-source-broker/projection` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_ACTION_PROPOSAL_DIR` | `/var/lib/pixel-source-broker/proposals` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_ACTION_RESULT_DIR` | `/var/lib/pixel-source-broker/results` | Generated/configured path; verify the installed host instead of assuming the example value |

## Operations Broker

| Configuration key | Repository example value | Evidence/privacy boundary |
|---|---|---|
| `PIXEL_OPS_BROKER_UNIT` | `pixel-ops-broker.service` | Service identity; verify the effective installed unit |
| `PIXEL_OPS_BROKER_SYSTEMD_DIR` | `/etc/systemd/system` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_BROKER_INSTALL_DIR` | `/opt/pixel-ops-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_BROKER_STATE_DIR` | `/var/lib/pixel-ops-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_POLICY_PATH` | `/etc/pixel-ops-broker/policy.json` | Private custody; do not copy or inspect contents for routine diagnosis |
| `PIXEL_OPS_STATE_DIR` | `/var/lib/pixel-ops-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_REQUEST_DIR` | `/var/lib/pixel-ops-broker/requests` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_RESULT_DIR` | `/var/lib/pixel-ops-broker/results` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_EVENT_DIR` | `/var/lib/pixel-ops-broker/events` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_CANCEL_DIR` | `/var/lib/pixel-ops-broker/cancel` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_OPS_INVENTORY_PATH` | `/var/lib/pixel-ops-broker/inventory.json` | Generated/configured path; verify the installed host instead of assuming the example value |

## Frontier Broker

| Configuration key | Repository example value | Evidence/privacy boundary |
|---|---|---|
| `PIXEL_FRONTIER_BROKER_UNIT` | `pixel-frontier-broker.service` | Service identity; verify the effective installed unit |
| `PIXEL_FRONTIER_BROKER_SYSTEMD_DIR` | `/etc/systemd/system` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_FRONTIER_BROKER_INSTALL_DIR` | `/opt/pixel-frontier-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_FRONTIER_BROKER_STATE_DIR` | `/var/lib/pixel-frontier-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_FRONTIER_POLICY_PATH` | `/etc/pixel-frontier-broker/policy.json` | Private custody; do not copy or inspect contents for routine diagnosis |
| `PIXEL_FRONTIER_CREDENTIAL_PATH` | `/var/lib/pixel-frontier-broker/private/provider-key` | Private custody; do not copy or inspect contents for routine diagnosis |
| `PIXEL_FRONTIER_STATE_DIR` | `/var/lib/pixel-frontier-broker` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_FRONTIER_REQUEST_DIR` | `/var/lib/pixel-frontier-broker/requests` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_FRONTIER_RESULT_DIR` | `/var/lib/pixel-frontier-broker/results` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_FRONTIER_EVENT_DIR` | `/var/lib/pixel-frontier-broker/events` | Generated/configured path; verify the installed host instead of assuming the example value |
| `PIXEL_FRONTIER_CANCEL_DIR` | `/var/lib/pixel-frontier-broker/cancel` | Generated/configured path; verify the installed host instead of assuming the example value |

For safe health checks, use [verify and diagnose](../operations/verify-and-diagnose.md). For credential and process boundaries, use [the security boundary reference](../security/boundary-reference.md).
