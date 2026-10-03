# Limb-kit pressure evaluation

`fuzz.py` creates a clean generated limb with Local, Operations, and Frontier policy-pack
skeletons, then applies deterministic hostile mutations. It covers gateway and worker
authority, paths, templates, retention, secrets, unknown files, signed-tool ownership,
fixed-helper execution, target placeholders, production grants, change rollback metadata,
linear-time parameter patterns, exact helper paths, tier/action alignment, typed Frontier
tasks, bounded semantic versions, and policy version/duplicate constraints.

Run from the repository root:

```bash
python3 security-evals/limb-kit-pressure/fuzz.py
```

The default 800 cases span 40 mutation classes and must all be rejected.
