# Public release checklist

This replaces the March 2026 workstation launch diary. It lists release gates;
an unchecked item is not a statement about the current tree, and this page does
not certify a release. Record results against one exact candidate SHA using
[Release validation](RELEASE_VALIDATION.md).

- [ ] Verify that published installation commands select the intended release
  channel and record the actual installed revision.
- [ ] Run the checks required by the [high-risk change map](HIGH_RISK_CHANGE_MAP.md)
  and [validation matrix](VALIDATION-MATRIX.md), recording skipped gates.
- [ ] Verify fresh install, update, reboot/recovery, and rollback on every
  platform included in release claims; a source test is not a hardware result.
- [ ] Review secret-scan coverage, candidate adjudication, workflow permissions,
  and required branch checks against the candidate.
- [ ] Review [mixed licensing](../LICENSING.md), contribution grants,
  third-party notices, model terms, and the rights of every distributed asset.
- [ ] Check every shipped public Markdown link, including vendor and extension
  documents, and confirm that external evidence is publicly accessible.
- [ ] Publish sanitized evidence with commit identity, artifact hashes, commands,
  outcomes, and explicit limitations; omit personal paths and private run IDs.
- [ ] Verify [platform claims](PLATFORM-TRUTH-TABLE.md), support docs, and release
  notes agree with the candidate's completed acceptance matrix.

Both the [repository license](../../LICENSE) and [ODS license](../LICENSE)
are present. Their existence does not override the separate Pixel and
third-party terms. For an operator diagnosis, follow
[ODS Doctor](ODS-DOCTOR.md) and [support bundle guidance](SUPPORT-BUNDLE.md).
