# Pixel source provenance in ODS

The source under `vendor/pixel/` was exported from the private
`Osmantic/Pixel` repository at commit
`b33730436baf5d98bf58f7d57c090318fe19f433`, then adapted for ODS's
public ODS-only license, bundled-source installation, documentation, and
22-tool integration. The export deliberately omits the private Git history,
the private repository's `.github` workflows, the historical `LIVE-AUDIT*`
documents, and `DREAM-FORGE-SOURCE-AUDIT.md`. It is not a live Git submodule.

The visible source is duplicated into `vendor/pixel.bundle` solely so existing
Pixel installation code can use exact-commit Git verification without network
or private credentials. The bundle contains one new synthetic root commit with
public Osmantic release identity and no ancestors. Its commit is
`6e82d4c974be8c7b5aebe3a4ffd5374e20ad0ac5`, and its SHA-256 is
`115da4c894a40991c40fc3f5ff94cb2763b4b9395d875e1b781f234d563fc79b`.
Run `python3 scripts/verify-pixel-bundle.py` to check the bundle against the
visible source, tracked executable modes, and those pins. The `pixel` launcher
and the install/bootstrap scripts must retain executable Git modes.

The repository owner authorized this source publication and the ODS-only
Pixel grant. Third-party packages are not included in the Git bundle and
retain their own notices and licenses.

The ODS lint maintenance adaptation retains import effects and exported names,
renames only unread local bindings without dropping their right-hand sides,
expands semicolon statements with AST-equivalence verification, removes one
identical shadowed test helper, and supplies two missing Python imports. The
bundle was regenerated twice from tracked source bytes and executable modes;
both artifacts matched. The public synthetic author, message, and original
synthetic timestamp are retained; no upstream/private history is included.

The ODS gateway-extension capacity adaptation raises the configure and renderer
per-extension limit from 24 to 32 tools, retaining name, digest, path and duplicate
validation. Native boundary tests cover 25 and 32 accepted tools and 33 rejected;
the ODS installer additionally configures the pinned bundle using its actual
generated extension tool list. The bundle retains its public synthetic identity
and timestamp, contains one root commit, and two regenerations were byte-identical.

## Pending upstream change: Anthropic work-provider model

Pixel's Anthropic work-provider lane is pinned to `claude-sonnet-4-5-20250929`.
Anthropic retires that snapshot no sooner than 2026-09-29. The profile's
`modelSelection` is `fixed`, and an owner-private policy may only repeat
`defaultModel`, so ODS cannot override it without regenerating this bundle.
The lane is off unless an owner-private policy enables it with the owner's own
Anthropic key; ODS never does. Portal chat uses LiteLLM's `ods/current` route.
The upstream Pixel fix is to change the ID to `claude-sonnet-4-6` in:

- `deploy/work-provider/profiles/anthropic.json` (`defaultModel`)
- `deploy/work-provider/neutral-corpus.mjs` (`ANTHROPIC_MODEL`)
- `deploy/work-provider/provider-smoke-core.mjs` (the `anthropic` smoke model)
- `tests/provider-ingress-smoke.test.mjs`, `tests/work-provider-adversarial.test.mjs`
  and `tests/work-provider-qualification-remote-lanes.test.mjs`
- `deploy/work-provider/README.md` and `CHANGELOG.md`

Owners who enabled the lane must update any `model` in their policy and requalify
the lane; the router rejects a qualification for another model. Pixel's OpenRouter
profile has a placeholder `anthropic/claude-sonnet-4-5` default, which is never
sent because that lane is owner-pinned. `tests/test-cloud-model-ids.py` tracks
both IDs and fails once a re-vendor drops them.
