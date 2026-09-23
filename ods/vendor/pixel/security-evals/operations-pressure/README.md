# Operations pressure loop

This harness repeatedly tests the source-projection and Operations Limb contracts:

- source safety: direct, encoded, Unicode-obfuscated, cross-source, malformed-field,
  and field-smuggling payloads across email, Calendar, and social projections;

- safety: malformed identities, path escapes, credential-shaped commands, SSRF URLs,
  unsafe filenames, invalid dependency graphs, policy boundary changes, and plan-hash
  invariants;
- capability: local and fake-SSH execution, isolated test runners, workflows,
  cancellation, failure propagation, output flooding/redaction, approvals, downloads,
  and verified artifact transfer.

Run a bounded loop on Linux:

```bash
./pixel pressure 10
```

Or continue until interrupted:

```bash
./pixel pressure forever
```

The harness writes a JSONL result outside the repository (under `/tmp` by default).
Override `PIXEL_PRESSURE_REPORT` to select another non-repository path. Every iteration
uses a recorded deterministic seed so a failure can be reproduced with
`python3 security-evals/operations-pressure/fuzz.py --seed SEED`.

`race.py` launches many independent broker objects against one authority state tree. It
requires a one-use lease ID to be granted exactly once and a persistent execution
budget to admit exactly its configured count under concurrent reservations. It parses
the resulting audit stream to detect torn cross-process writes.

This loop is deliberately local/disposable. It does not contact production targets,
approve plans, rotate keys, or mutate a client deployment. Live target exercises remain
an explicit acceptance step under `OPERATIONS-LIMB.md`.
