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
`55837c2d1231a7d0a36f82975d3069e754cc413f`, and its SHA-256 is
`bea663dc7a3788d737912dc28e1b7768a2403f9f19bc58d26580f40ec04af1b4`.
Run `python3 scripts/verify-pixel-bundle.py` to check the bundle against the
visible source, tracked executable modes, and those pins. The `pixel` launcher
and the install/bootstrap scripts must retain executable Git modes.

The repository owner authorized this source publication and the ODS-only
Pixel grant. Third-party packages are not included in the Git bundle and
retain their own notices and licenses.
