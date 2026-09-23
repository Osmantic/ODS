# Runner boundary live probe

Run this on the Operations Broker host after enrolling or refreshing a target and after
installing that target's action configuration:

```bash
bash security-evals/runner-boundary/run-live.sh TARGET_ID OPERATOR_SSH_ALIAS EXPECTED_HOSTNAME
```

The probe is non-destructive. It proves that the broker key reaches only Pixel's forced
command, arbitrary command and shell attempts fail, SSH transport and hostile workload
use different Unix identities, the workload has no SSH key or root sudo authorization,
and both the unprivileged and typed managed routes remain usable. It prints no key or
credential material.

Before release, exercise the full account and sudo provisioning in a disposable pinned
Linux image:

```bash
bash security-evals/runner-boundary/run-clean-room.sh
```

This installs only inside the disposable container and verifies that hostile workload
code cannot invoke the root helper directly or through sudo while both authorized
transport routes still work.
