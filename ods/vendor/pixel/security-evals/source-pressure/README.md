# Source projection pressure test

This deterministic harness generates direct, encoded, Unicode-obfuscated,
cross-source, and field-smuggling payloads across email, Calendar, and social
projections. It also feeds malformed upstream field shapes. Every case must be
quarantined, omit its unique canary from projected text, remain bounded, and
discard unknown nested fields.

```sh
python3 security-evals/source-pressure/fuzz.py --iterations 5000 --seed 20260805
```

The canaries are inert local strings. The harness performs no network calls and
no Calendar mutations.
