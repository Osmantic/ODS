# ODS-maintained Pixel 4.3.34 candidate

Functional source checkpoint: `096eba7f81d4bbbd588863b367fac343ecf6c86f` on the
public ODS branch `feat/pixel-4.3.34-source-broker`. This record does not claim
an upstream Pixel release or completed fleet acceptance.

The Source Broker gains what an owner-facing Google connection needs before it
can be turned on: Calendar pagination with window and truncation coverage,
per-source refresh isolation with a content-free `status.json`, granted-scope
handling, a broker-identity sign-in exchange and revocation, and a `--prepare`
install mode. The exchange records only the account that Google's ID token
verifies. Every release now carries the plugin's locked `typebox` dependency,
because a source turned on after install loads the plugin from the active
release; this adds files to the release tree and changes nothing else. The
gateway environment now carries the web and Calendar-write flags, and three
optional onboarding keys (`calendarWriteEnabled`, `gmailWindowDays`,
`userTimezone`) default to the previous behaviour. The version advances from
4.3.33 to 4.3.34 because the existing installer rejects different source at the
same version. OpenClaw, Node, plugin dependency versions, base-image digests,
policies and trust anchors are unchanged.

ODS writes every source limb off, so on ODS installations none of the new broker
code runs, and the rendered `openclaw.json` is unchanged. A standalone deployment
that leaves the new keys out renders the same configuration as 4.3.33; the
vendored e2e suite compares the default and explicit renders byte for byte.

Automated evidence, all with fakes and no network or real accounts:

- `tests/test_source_broker.py`: pagination and truncation, per-source isolation,
  each error class, status continuity across runs, missing granted scopes, the
  sign-in exchange (good path, wrong state, rejected code or verifier, endpoint
  and redirect pinning, expiry, file modes, handoff deletion) and revocation.
  The new cases fail against the 4.3.33 broker.
- `tests/test_release_source_plugin.py`: a release built with the broker off
  carries the plugin dependency; offline test builds can still skip npm.
- `tests/source-broker-plugin.test.mjs`: Calendar change tools register only
  while writes are on; list results carry coverage and sync state.
- `tests/test_email_prompt_injection.py` and `tests/test_source_pressure.py` pass
  unchanged.
- `tests/e2e.sh`: generated units and environment files, and renders with writes
  on, off, and with a user time zone.

An independent review before publication found and fixed: a disconnected sign-in
kept serving the last snapshot; Calendar busy checks missed events already running;
revocation could leave the token after an unexpected reply; restore verified before
the first refresh wrote `status.json`; an older staged token could replace a newer
one in custody; and the control UI chat did not inherit the new flags. Each has a
regression test.

Before accepting this release, run the held source upgrade on real Linux/WSL
installations and through a macOS native update, verify the installed source
version, and confirm that a disconnected ODS install keeps the same onboarding
contract, `openclaw.json` and bootstrap bytes. Exercise a real Google connection
only with a dedicated test account. The release remains a candidate until that
evidence is recorded. No qualification signature or private upstream source was
used.
