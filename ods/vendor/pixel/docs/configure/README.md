---
title: Configure Pixel
doc_type: how-to
audience: [owner, operator]
feature_status: mixed
owners: [documentation, configuration]
sources_of_truth: [DEPLOYMENT.md, onboarding.example.json, .env.example, scripts/configure.mjs, scripts/plan.sh]
last_verified_at: 2026-08-27
---

# Configure Pixel

Configuration converts reviewed owner inputs into private environment and generated deployment files. It is not activation.

## Choose an authoring path

- Use `./pixel ui` for ordinary credential-free onboarding and its exact fixed configure preview/action.
- Use an untracked private copy of `onboarding.example.json` for advanced paths, external credential bindings, policies, and signed packs.
- Use interactive `./pixel configure` only when a terminal questionnaire is preferable.

For private JSON:

```bash
./pixel configure --answers /secure/path/onboarding.json
```

Configuration validates field shapes, profiles, paths, URLs, policy relationships, pack identities, and secret-presence boundaries. It preserves a mode-`0600` canonical onboarding copy, writes ignored `.env`, and renders ignored `.generated` artifacts. Private credentials remain in ignored or external custody and are not copied into documentation or release state.

Re-running requires `--force`. Review the new input and preserve required advanced private fields before replacing the generated configuration.

## Review before activation

```bash
./pixel plan
```

Plan checks the selected host, endpoints, schemas, images/plugins, required secret presence, source identity, and rendered deployment. Review its summary and exact hash. If any authored/private input changes, build a new plan.

## Configuration layers

| Layer | Examples | Owner action |
|---|---|---|
| Authored public source | examples, profiles, schemas, release manifest | Change in Git under review |
| Private authored input | onboarding, policies, credential paths, overlay | Keep owner-only and outside Git |
| Generated deployment | `.env`, `.generated`, rendered units/config | Recreate with configure; do not hand-edit |
| Runtime state | installed config, services, broker state, projections | Change through apply/owning lifecycle commands |
| Evidence | plan hash, runtime attestation, receipts | Validate exact identity/freshness; no authority by existence |

Use the generated [configuration source map](../reference/configuration.md) to locate each example and environment key.

## Task guides

- [Deployment profiles](deployment-profiles.md)
- [Capability profiles](capability-profiles.md)
- [Models and providers](models-and-providers.md)
- [Source limbs](source-limbs.md)
- [Operations and Frontier](operations-and-frontier.md)
- [Client overlays](client-overlays.md)

For the explicit keyless search option and its installer requirements, see [Native search providers](../NATIVE-SEARCH.md).
