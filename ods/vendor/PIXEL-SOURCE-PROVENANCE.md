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
`69f4ad0bd062fe006e9d5b473a04b9a38eff8533`, and its SHA-256 is
`d01ae60047b9c8a07a66547985f1ee3ca96342b401115dc2f9b4603e96c9a3a6`.
Run `python3 scripts/verify-pixel-bundle.py` to check the bundle against the
visible source and those pins.

The public export records its omitted historical evidence in
`pixel/PUBLIC-SOURCE-EXPORT.json`. Documentation generators label that evidence
as unavailable in this export and do not publish broken links or treat its
absence as a verified qualification result. Unlisted missing evidence remains
an error. This documentation repair regenerated the synthetic root commit;
the preceding public bundle was `817214d5ec3d8aa583fe50c1dc7561f3c1a16dff`.

The repository owner authorized this source publication and the ODS-only
Pixel grant. Third-party packages are not included in the Git bundle and
retain their own notices and licenses.
