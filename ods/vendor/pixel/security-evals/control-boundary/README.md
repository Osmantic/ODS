# Control boundary live test

`run-live.py` proves both sides of the deployed loopback API boundary. Requests with
no credential or a forged credential must be rejected, while the real credential must
complete one exact, non-tool capability probe. Model-inserted formatting whitespace is
ignored, but any additional non-whitespace content fails. The test also fails if the credential
appears in any response or in recent gateway service logs.

It reads the credential only inside its own process, never places it on a command line,
and emits a secret-free JSON result. Run it on the deployment owner account after
`./pixel verify`:

```bash
python3 security-evals/control-boundary/run-live.py
```

The authenticated path consumes one model turn. It makes no external request and does
not exercise a source or Operations limb.
