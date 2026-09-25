---
title: Add an Operations action pack
doc_type: how-to
audience: [contributor, operator, security-reviewer]
feature_status: candidate
owners: [documentation, operations, security]
sources_of_truth: [CUSTOMIZATION.md, OPERATIONS-LIMB.md, scripts/limb-kit.py, scripts/configure-ops-action-pack.mjs, schemas/operations-action-pack-v1.schema.json]
last_verified_at: 2026-08-27
---

# Add an Operations action pack

An Operations action pack declares reusable namespaced fixed-helper actions with placeholder targets. It is inert until each placeholder is mapped in private onboarding to target IDs already present in the deployment-owned Operations policy.

Use `./pixel limb-kit add-operations` to create the signed-pack skeleton. Review:

- exact fixed helper argv and anchored parameter patterns;
- honest read/staging/managed/change effect tier;
- target placeholder, working-root, time, output, concurrency, artifact, and failure budgets;
- deterministic verification and rollback for managed changes;
- any proposed grants, which must not silently create production/change/break-glass autonomy.

After signing/installing the containing limb, bind targets through the limb-kit lifecycle or the exact base command:

```bash
# Requires exact confirmation; review before running.
./pixel ops-action-pack /secure/client/onboarding.json PACK.json \
  PLACEHOLDER TARGET_ID --confirm
```

Then reconfigure, plan, apply, and verify. The base Operations policy, target pins, grants/leases, budgets, approvals, execution helper, and results remain authoritative.

Reject shell fragments, interpreter-eval flags, generic sudo, unbounded paths/output, inaccurate reversibility, foreign targets, and duplicate action names. Use `unbind-operations` before disabling/removing a mapping. A signed declaration or target mapping is not proof that any target command ran.
