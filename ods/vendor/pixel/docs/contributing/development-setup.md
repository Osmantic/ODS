---
title: Pixel development setup
doc_type: tutorial
audience: [contributor, maintainer]
feature_status: supported
owners: [documentation, maintainers]
sources_of_truth: [CONTRIBUTING.md, .node-version, scripts/bootstrap.sh, tests/static.sh, tests/run.sh]
last_verified_at: 2026-08-27
---

# Pixel development setup

Pixel is security-sensitive and its complete supported-host checks target Ubuntu 24.04 LTS and Debian 12. Documentation and many deterministic unit checks can run elsewhere, but that does not qualify another deployment host.

## Prepare a clean checkout

```bash
git clone https://github.com/Osmantic/ODS.git ods
cd ods/vendor/pixel
git status --short --branch
```

Work from a current ODS branch based on `main`. Keep private deployment and runtime material outside the checkout.

## Install toolchain prerequisites

The static gate requires Bash, Git, `rg`, Node matching `.node-version`, and Python 3. Deployment/e2e families may also require Docker, `age`, systemd, sudo/PAM, and subsystem-specific fixtures.

Use bootstrap to inspect before changing the host:

```bash
./pixel bootstrap
```

Review missing packages before `./pixel bootstrap --apply`. Do not run live/provider/target harnesses merely to establish a development shell.

## Fast orientation

```bash
./pixel help
node scripts/docs/check.mjs
```

Read the [codebase map](codebase-map.md), select a row from the [high-risk change map](high-risk-change-map.md), and identify the exact source, schema, tests, documentation, rollback, and live evidence affected before editing.

## Focused test loop

For a Node test:

```bash
node --test tests/RELEVANT.test.mjs
```

For a Python unittest module:

```bash
python3 -m unittest tests/test_relevant.py
```

For shell syntax or a focused shell harness, run the exact script named by the owning test family. Add a regression that fails before the fix.

## Full local gate

On a supported development host:

```bash
./pixel test
./pixel package
```

`./pixel test` composes the static and e2e gates. Packaging is a deterministic artifact check; it is not signing, publication, clean-host qualification, or live acceptance.

Security changes require two consecutive clean full passes after the final relevant edit, plus the focused pressure, race, isolation, recovery, or live harnesses for the changed boundary.
