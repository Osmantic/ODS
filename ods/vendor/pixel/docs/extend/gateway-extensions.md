---
title: Add a Pixel gateway extension
doc_type: how-to
audience: [contributor, security-reviewer, maintainer]
feature_status: supported
owners: [documentation, architecture, security]
sources_of_truth: [README.md, onboarding.example.json, scripts/configure.mjs, scripts/hash-gateway-extension.mjs, tests/secure-plugin-read.test.mjs]
last_verified_at: 2026-08-27
---

# Add a Pixel gateway extension

Gateway extensions are code-bearing OpenClaw plugins explicitly allowlisted in private onboarding. They are different from data-only signed policy packs and from separately isolated source/Operations/Frontier brokers.

## Bundled versus custom

A Pixel-pinned bundled plugin uses an allowlisted ID-only entry. A custom plugin requires:

- a unique safe ID outside Pixel-managed namespaces;
- an absolute read-only path outside gateway-writable state, workspace, and cache;
- the exact tree digest from:

```bash
./pixel extension-hash /absolute/reviewed/plugin-directory
```

- an explicit list of any custom Pixel tool names.

Configuration rejects duplicate IDs/tools, unpinned ambient plugin IDs, managed plugin overrides, unsafe paths, reserved tool namespaces, tree/hash drift, links or files outside the reviewed contract. Loading a plugin does not grant its tools unless they are separately declared.

## Review requirements

Treat a custom gateway extension as high-risk code inside the model-facing process. Review every byte, dependency, manifest, hook, tool schema, input/output bound, filesystem/network behavior, credential access, update source, failure path, and removal behavior. It must not be used to bypass the dedicated broker/actuator boundary for credentials, external effects, machine work, or provider egress.

## Apply and remove

Update private onboarding, reconfigure with `--force`, build a new plan, inspect the extension ID/path/hash/tools, apply, verify plugin resolution against the active immutable release, and run focused negative tests. To remove, delete the authored extension entry, repeat the same transaction, then process plugin files under the client's retention policy. Configuration removal is not credential revocation.
