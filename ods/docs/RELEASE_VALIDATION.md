# Release Validation

Last updated: 2026-05-25

ODS is validated as an installed appliance, not only as a collection of
unit tests. The release-grade path combines CI, clean distro bootstrap checks,
a distro lab, and a private real-hardware fleet so operational changes are
tested against the surfaces users actually touch: the public install command,
service startup, dashboard flows, model routing, Hermes, full-model
capabilities, reinstall, restart, and `ods doctor`.

This document is a public, sanitized summary of that release gate. It describes
what a green run proves without publishing private hostnames, LAN addresses,
usernames, local paths, or raw run logs. For the broader hardware and distro
surface, see [VALIDATION-MATRIX.md](VALIDATION-MATRIX.md). For day-to-day local
test commands, see [TESTING.md](TESTING.md).

## When We Run It

Run the release-grade fleet after operational code changes: installer phases,
bootstrap logic, Docker Compose stack generation, service manifests, dashboard
API behavior, Hermes, model routing, GPU/runtime detection, lifecycle commands,
or anything that can affect a user's install or running stack.

Docs-only, comment-only, and narrow test-only changes usually use focused
validation instead. Dependency or runtime wiring changes should use the
release-grade gate even when the code diff is small.

PRs should state their changed surface and validation level explicitly. Use
[HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md) to decide whether focused
checks are enough or whether the candidate needs release-grade fleet validation
before it is treated as releasable.

## User Green

`User Green` is the top-level release-readiness result. It is not a marketing
claim that every possible machine or network will work. It means the enabled
release surfaces passed or were explicitly accounted for in the current
candidate run.

| Gate | What it proves |
|---|---|
| Zero-prereq bootstrap | Bare Linux distro containers can fetch the public installer and provision missing prerequisites such as Git, Python, Docker, and Compose. |
| Install Green | Enabled real-hardware hosts can fresh-install from the public bootstrap path. |
| Product Green | Core services, cloud-mode contracts, dashboard flows, Hermes auth/chat, and UI checks pass after install. |
| Capability Green | Full-model capability probes pass after large model downloads and swaps complete. |
| Model Switchboard Green | Model-management release coverage proves the six-model matrix, app probes, visible agent gates, and coverage ledger for each reachable host. |
| Lifecycle Green | Idempotent reinstall, `ods restart`, and `ods doctor` recover cleanly after state changes. |
| User Green | The combined release gate is clean, with failures, skips, and deferrals resolved or documented. |

## Release-Grade Surfaces

The release harness normally combines these layers:

| Layer | Coverage | Why it matters |
|---|---|---|
| CI | Fast syntax, contract, dashboard, shell, Python, and PowerShell checks | Catches cheap regressions before hardware time is spent. |
| Zero-prereq bootstrap | Clean Ubuntu, Debian, Fedora, Rocky, Arch, and openSUSE containers | Proves the public `curl` path does not assume a developer workstation. |
| Distro lab | 10 Linux container lanes plus systemd-capable Incus VM lanes | Exercises package-manager, systemd, Docker daemon, and Compose behavior across distro families. |
| Real hardware fleet | Linux NVIDIA, Linux AMD/ROCm-Lemonade, ARM Linux NVIDIA, and Apple Silicon hardware classes | Proves accelerator/runtime behavior and the installed product on actual machines. |

The latest release-grade fleet run for the current candidate should be cited in
release notes with its commit, date, enabled hardware classes, and any skipped
or deferred surfaces. Public docs should summarize the sanitized evidence
rather than linking raw private run artifacts.

## What We Check

A release-grade run includes:

- public bootstrap and zero-prereq install checks;
- fresh install on enabled real-hardware targets;
- core service health and generated config contracts;
- cloud and hybrid mode contracts;
- dashboard API flows such as model download, model switch, and extension install;
- Hermes authentication and agent chat with seed verification;
- browser UI checks on the default UI target;
- full-model capabilities such as chat, search, file read/write, code execution,
  skills, model identity, and context;
- lifecycle checks: idempotent reinstall, `ods restart`, and `ods doctor`;
- regression replay for previously fixed fleet failures.

Model-management coverage has two named tiers:

| Tier | Required coverage |
|---|---|
| Release | Six distinct planned test models per reachable host. Each model must run the full verb chain: discover, download, load, use through enabled LLM apps, restore, and delete or disclose retained state. |
| Smoke | One planned test model per host running the same full verb chain. Smoke is suitable for iteration confidence, not for final User Green. |

The release tier is a six-model matrix, not six repeats of the same model. The
harness must record the planned target for each cycle and reject target drift or
same-model reuse unless the report explicitly discloses and justifies the
deviation.

Capability probes can be deferred while a large model is still downloading or
hot-swapping. The capabilities watcher polls until the model is ready, reruns
the probes, and updates the report so a release is not marked User Green just
because the first pass arrived before the model did.

## Coverage Ledger

Model-management and Switchboard runs should append a machine-readable coverage
ledger entry for each host/cycle. The ledger is release evidence, not an
operator scratchpad. Each entry should include:

| Field | Meaning |
|---|---|
| `timestamp` | When the cycle result was finalized. |
| `product_sha` | Frozen product commit under test. |
| `harness_sha` | Harness commit used to run and adjudicate the result. |
| `tier` | `release` or `smoke`. |
| `host_id` | Sanitized host identifier. |
| `platform` | OS, hardware class, GPU/backend, runtime mode, and relevant model runtime. |
| `cycle` | Cycle number and planned target model id. |
| `distinct_model_count` | Distinct planned test models completed for that host at the stamped SHA. |
| `verbs` | Status and artifact links for discover, download, load, use, restore, and delete. |
| `apps_discovered` | Enabled LLM consumers found from manifests, env/config references, or known core services. |
| `app_probes` | Per-app probe status, endpoint, auth disposition, and model identity evidence. |
| `gates` | Context/capability gates shown to the user, including agent viability gates. |
| `open_webui_auth` | Credential strategy and result for Open WebUI. A 401/login wall is red or deferred, never pass. |
| `re_adjudication` | Any recomputation after an earlier failure, with original failure path and reason. |
| `drift` | Hand repairs, skipped cleanup, target changes, or local operator intervention. |
| `result` | Pass, fail, or deferred with a concrete reason and owner when deferred. |

The release report should fail User Green when release-tier ledger coverage is
missing, when the host has fewer than six distinct completed test models, or
when app probes silently skip an enabled LLM consumer.

## Re-Adjudication Rules

If a run is recomputed after an `OVERALL: FAIL`, the updated report must say so.
Valid re-adjudication includes a logged false-red reproduction, the old result,
the corrected rule, and the affected artifacts. It is not valid to relax a
validator silently or to convert missing app authentication into a pass.

Open WebUI is a required probe when enabled. The harness must either provision a
known admin/API credential and prove the active model through Open WebUI, or
record the lane as failed/deferred with the auth blocker. HTTP 401, a login
page, or an unauthenticated health page does not prove model viability.

## Known Limits

- ODS Talk and owner-card probes only gate when the owner-card surface is
  enabled and `ods-proxy` is actually available, such as a LAN-enabled install.
  Default non-LAN installs skip those probes instead of false-failing a surface
  the user did not expose.
- Vision probes are opt-in and should be called out explicitly when enabled for
  a candidate run.
- Incus Arch and openSUSE VM lanes can hit nested Docker limitations in the lab.
  The container lanes and real-hardware fleet are used to separate those lab
  limitations from product regressions.
- The fleet is not an exhaustive promise for every driver, firewall, storage
  layout, Docker Desktop version, home router, or unsupported hardware
  combination.
- A green release run is a strong release signal, not a soak test. Long-running
  thermal, benchmark, and overnight stability evidence is tracked separately.

## Reading A Green Run

A current User Green pass should give contributors and auditors high confidence
that the main supported install paths are working at release time:

- clean machines can reach the installer through the public bootstrap path;
- supported hardware classes can install and start the product;
- users can exercise dashboard, Hermes, model, and extension workflows;
- full-model AI capabilities are validated after downloads complete;
- reinstall, restart, and diagnostic paths recover after state changes.

It should not be read as a claim that all optional modes ran in that candidate.
Release notes should name any skipped or opt-in surfaces, especially Windows
fleet targets, ODS Talk owner-card flows, vision probes, AP mode, and network
topologies that vary by lab.
