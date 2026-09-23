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
`817214d5ec3d8aa583fe50c1dc7561f3c1a16dff`, and its SHA-256 is
`8fea465b1b42d82da0a286936d0e029b038321fd39793f5a849843ef11aee865`.
Run `python3 scripts/verify-pixel-bundle.py` to check the bundle against the
visible source and those pins.

The repository owner authorized this source publication and the ODS-only
Pixel grant. Third-party packages are not included in the Git bundle and
retain their own notices and licenses.
