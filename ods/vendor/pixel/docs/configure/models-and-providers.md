---
title: Configure Pixel models and providers
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, configuration]
sources_of_truth: [onboarding.example.json, .env.example, scripts/configure.mjs, DEPLOYMENT.md, CODEX-WORK-PROVIDER.md]
last_verified_at: 2026-08-27
---

# Configure Pixel models and providers

The primary Pixel model is identified by provider, model ID/name, private base URL, reasoning mode, context window, output ceiling, and credential binding. Record what the endpoint actually serves; do not infer capability from a marketing name.

## Primary model

In private onboarding, review:

- `modelProvider`, `modelId`, and `modelName`;
- `modelBaseUrl` and any explicitly attested private hosts;
- `modelReasoning`, `modelContextWindow`, and `modelMaxTokens`;
- `modelApiKey` custody or supported secret-provider binding;
- `openclawBin`, `openclawHome`, and fixed `agentId` used by owner chat.

A fresh deployment must provide required credential material through ignored `.env` or a supported external provider. Existing deployments may preserve an existing credential without placing it in onboarding. Never commit the key or put it in a browser field.

Configuration validates shape and privacy relationships. Planning checks reachability/identity prerequisites. Verification binds configured model identity, but a [first real conversation](../use/first-conversation.md) is required to prove an actual response.

## Frontier provider

Frontier is a separate optional broker with its own authentication mode, credential custody, provider policy, budget, qualification, and egress boundary. ChatGPT-plan access and separately billed API access are distinct; local Pixel budgets do not replace provider-side allowances or billing controls.

## Deep Work provider source

Multi-provider and Deep Work model source is separate from the primary conversational model. It is local-only by default and requires private policy, qualification, credential custody, and routing authorization. Current Deep Work runtime status remains [development-disabled](../status.md); provider adapters in source do not enable it.

## Change and rollback

After any model/provider change, reconfigure, build a new plan, apply, verify the installed identity, and run a harmless real model turn. Roll back through the deployment transaction and revoke replaced provider credentials at their issuer when appropriate.
