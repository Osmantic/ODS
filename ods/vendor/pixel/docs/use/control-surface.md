---
title: Use the Pixel control surface
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, product, security]
sources_of_truth: [CONTROL-SURFACE.md, README.md, control/server.py, control/ui/app.js, control/policy.example.json]
last_verified_at: 2026-08-27
---

# Use the Pixel control surface

`./pixel ui` starts an owner-private, dependency-free process bound to exact IPv4 loopback. Its default workspace is conversation; the secondary control center provides setup, status, evidence orientation, budgets, recovery guidance, and policy-bound fixed actions.

```bash
./pixel ui
```

Open the exact printed process-lifetime URL on the same host. Stop the process when it is not needed. Do not expose the listener, reuse an old URL, or put its review fragment in logs, bookmarks, tickets, or messages.

## What the page can do

Always-available fixed actions can:

- save validated public onboarding choices while preserving advanced private fields;
- preview and run the fixed configuration action;
- build the fixed review plan; and
- run deployment verification.

Each mutation uses an exact, expiring, single-use preview bound to current private/public state. Output stays in owner-only bounded logs; the browser receives a content-free result.

A separately installed owner-only policy may enable public update checks, encrypted backup creation, one-way Operations/Frontier/Deep Work pause, and other explicitly listed fixed actions. Legacy or absent policy keeps them disabled.

## What the page cannot do

The page has no field or endpoint for credentials, arbitrary files, paths, shell, generic commands, arbitrary agents, deployment activation, source/Operations/Frontier approval, backup restore/decryption, credential rotation, update activation/rollback/recovery, or unrestricted broker resume.

Prepared configuration is not active. A content-free “backup creation succeeded” control result does not validate the archive. A recorded pause result does not prove current broker containment. Follow the terminal runbook.

## Conversation state

The composer is enabled only for the authorized exact URL and a safely verified configured agent. Turns expose bounded user/assistant text and content-free run/tool state. Failed or interrupted turns claim no answer. See [first conversation](first-conversation.md).

## Frontier and Deep Work views

The Frontier review view is separately policy-gated and content-bearing; it can show only exact awaiting-approval sanitized capsules and cannot approve them. A custom budget page can draft a short-lived proposal but cannot apply/activate it.

Deep Work status, authoring, semantic review, and lifecycle controls are separately policy/configuration gated. Current runtime remains [development-disabled](../status.md). Inert drafting/preparation/status does not create execution authority.

## Remote adapter boundary

The source tree includes a narrow authenticated origin adapter with separate workspace and approval audiences. Its current qualification status is recorded in the release contract. Never point a tunnel directly at `./pixel ui`; source presence is not a supported remote deployment claim.
