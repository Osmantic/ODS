---
title: Pixel capability status model
doc_type: concept
audience: [owner, operator, contributor]
feature_status: mixed
owners: [documentation, product, release]
sources_of_truth: [RELEASE-MANIFEST.json, QUALIFICATION-MATRIX.json, OPENCLAW-COMPATIBILITY.json, CONTROL-SURFACE.md, schemas/control-status-v1.schema.json]
last_verified_at: 2026-08-27
---

# Pixel capability status model

Status answers a bounded question about one surface. It is not a single product-wide badge.

## Feature-status vocabulary

| Status | Meaning |
|---|---|
| Supported | Exact compatibility and qualification evidence assigns this status for the named scope |
| Candidate | Evidence is under review; merge, publication, installation, activation, and live acceptance remain separate |
| Development-disabled | Source may exist, but runtime authority is intentionally absent |
| Synthetic-only | A declared synthetic contract passed; no real backend is implied |
| Historical | Evidence describes an earlier exact state |
| Mixed | The page covers surfaces with different statuses and labels each one |

The generated [current status](../status.md) is the repository-level authority.

## Operational state vocabulary

| State | Meaning | Does not mean |
|---|---|---|
| Disabled | Configuration/policy intentionally omits the surface | Broken or unsupported |
| Not configured | Required authored/private inputs do not exist | Model/provider failure |
| Prepared | Coherent generated settings or an inert candidate exist | Active, contacted, or accepted |
| Active | Fresh exact runtime evidence for that named surface is verified | Every capability is healthy or qualified |
| Paused | New authority is stopped by its durable boundary | Existing effects are rolled back or all workers are terminated |
| Attention | A failed/interrupted/uncertain state needs review | Automatic retry is safe |
| Unavailable | Pixel could not validate required evidence safely | Disabled, absent, or healthy |

## How to report status

Name the surface, scope, host, exact evidence, observation time, and next missing boundary. For example, “Frontier is prepared but awaiting external authentication and plan validation” is useful; “Pixel is ready” is not.

Never downgrade `unavailable` to `disabled`, or upgrade `prepared` to `active`, merely to simplify a dashboard. Missing or contradictory evidence fails closed.
