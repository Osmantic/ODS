---
title: Pixel deployment profiles
doc_type: reference
audience: [owner, operator]
feature_status: mixed
owners: [documentation, configuration]
sources_of_truth: [onboarding.example.json, scripts/configure.mjs, scripts/reference-services.sh, profiles/, DEPLOYMENT.md]
last_verified_at: 2026-08-27
---

# Pixel deployment profiles

The deployment profile selects infrastructure, not agent authority.

| Profile | Meaning | Operator responsibility |
|---|---|---|
| `prepared` | Use separately operated private model and search endpoints | Provision, secure, monitor, back up, and prove endpoint reachability independently |
| `reference` | Render Pixel's digest-locked reference model/search services | Provide Docker/capacity, review images, and operate the rendered services |

Choose the profile in onboarding. For the reference profile:

```bash
# Requires exact confirmation; review before running.
./pixel services up --confirm
./pixel services status
```

For the prepared profile, do not start reference services. Confirm the configured URLs remain private and available to the deployed service identity.

Changing profiles requires reviewed onboarding, `configure --force`, a new plan, confirmed apply, and verification. Roll back through the deployment transaction; do not hand-edit generated compose or service files.

Neither profile proves model fit, answer quality, image pull success, or host qualification. Use Doctor for advisory capacity and a real configured-model turn for backend evidence.
