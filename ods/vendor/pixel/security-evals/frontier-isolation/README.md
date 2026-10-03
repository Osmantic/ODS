# Frontier deployment-isolation evaluation

This destructive test runs only inside a disposable Docker container. It builds a pinned
Codex CLI image, then disables container networking before qualification and execution.
No real provider credential is used and no paid provider request is possible. The
harness exercises both supported authentication modes with synthetic fixtures:

- a one-line API key; and
- a minimal ChatGPT auth cache that is accepted only for the offline
  `codex login status` identity check.

Run from the repository root:

```bash
bash security-evals/frontier-isolation/run.sh
```

Override `PIXEL_FRONTIER_TEST_CODEX_VERSION` to qualify a candidate Codex upgrade. The
test creates real Linux users and ACLs inside the container, installs the Frontier
service with a fake `systemctl`, runs the loopback-only Codex tool-manifest probe, and
verifies:

- API-key credentials, their containing directory, and policy are root-owned, with
  only the required broker-group read/traverse access;
- the broker can read but cannot modify or replace an API key;
- ChatGPT mode validates a broker-owned, mode-0600 persistent auth cache with a
  mode-0700 directory, and the installed Codex CLI reports ChatGPT as the active
  login method;
- switching authentication modes removes the previous mode's credential residue;
- temporary grant staging uses a root-owned runtime directory, and grant/show/revoke
  operator commands work without leaving the staged input behind;
- the gateway identity can publish requests and read result/event projections;
- the gateway can read only the content-free rolling usage projection, and routing
  receipts contain enumerated local-attempt evidence rather than task content;
- the gateway cannot read credentials, policy, request archive, plans, approvals,
  authority, or provider runtime;
- a gateway-owned typed request is atomically claimed by the broker and becomes a
  private plan plus a readable content-free result;
- the production live-qualification CLI creates a private short-lived API consent,
  prepares only its fixed synthetic proposal, and—while container networking is
  disabled—emits a bounded failure receipt with at most one observed attempt;
- replaying that exact live confirmation returns identical content-free evidence and
  leaves exactly one matching usage record, rather than starting another attempt;
- the generated service contains the required process, filesystem, and path hardening.

The network-disabled live-qualification case is deliberately expected to fail at the
provider edge. It proves consent, payload, approval, accounting, and recovery behavior;
it is not a substitute for a deployment-owned live passing receipt.

The Docker build requires network access to fetch the pinned base image, Debian test
packages, and Codex package. The actual evaluation container runs with `--network none`.
