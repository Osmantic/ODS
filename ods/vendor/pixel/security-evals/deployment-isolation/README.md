# Deployment isolation clean-room test

`run-clean-room.sh` creates two disposable client configurations with distinct homes,
ports, gateway credentials, backup signing keys, age recipients, and private-state
roots. It proves both clients can validate their own backup, then proves cross-client
signature trust and cross-client restore-path substitution are denied. All canaries are
synthetic and the temporary environment is removed on exit.

Run on a supported Linux assurance host with the repository test fixture on `PATH`:

```bash
bash security-evals/deployment-isolation/run-clean-room.sh
```
