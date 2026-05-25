# Installer Phase Contracts

Dream Server's Linux installer is an ordered, sourced Bash pipeline. This
document records the operational contract for each phase so maintainers and fork
operators can review changes without rediscovering the installer from scratch.

For the file map and mod recipes, see
[INSTALLER-ARCHITECTURE.md](INSTALLER-ARCHITECTURE.md).

## Global Rules

- Installer libraries in `installers/lib/*` should define functions and
  constants only.
- Phase files in `installers/phases/*` execute when sourced.
- Phases share one Bash namespace through `install-core.sh`.
- A phase should mutate only the surfaces it owns.
- Re-running the installer should preserve generated secrets and user data
  unless a force/reset path explicitly says otherwise.
- Linux, macOS, Windows, host-agent, and bootstrap-upgrade writers must stay in
  sync for shared generated config.

## Phase Contracts

| Phase | Owns | Inputs | Outputs / mutations | Idempotency expectation | Common failure modes |
|---|---|---|---|---|---|
| 01 preflight | User, OS, shell, basic command checks | CLI flags, host OS, current user | Early warnings/errors, phase state | Safe to rerun | Unsupported shell, root/admin mismatch, missing base tools |
| 02 detection | Hardware and backend detection | GPU tools, CPU/RAM, backend contracts | Hardware tier, backend choice, compose hints | Safe to rerun | Missing drivers, WSL/container ambiguity, unsupported GPU |
| 03 features | Feature/profile selection | CLI flags, non-interactive mode, defaults | Enabled service/profile choices | Preserve explicit user choices | Interactive prompt in automation, invalid feature combo |
| 04 requirements | Resource and port preflight | Tier, selected services, disk/RAM/ports | Warnings or blocking errors | Safe to rerun | Port conflict, low disk, low memory, stale service holding a port |
| 05 docker | Docker and runtime prerequisites | OS/package manager, GPU backend | Docker, Compose, GPU runtime packages | Do not reinstall unnecessarily | Package repo failure, daemon unavailable, NVIDIA/ROCm toolkit mismatch |
| 06 directories | Filesystem layout and generated config | Repo path, `.env.example`, selected services | Install dirs, `.env`, generated config, data dirs | Preserve secrets and user state | Permission mismatch, stale generated config, missing data dirs |
| 07 devtools | Optional developer tools | Feature flags, user shell | Codex/Claude/OpenCode helpers | Skip already-installed tools | Network failure, unsupported host shell |
| 08 images | Image pull/build plan | Compose set, service manifests, GPU backend | Pulled/built images | Resume pulls/builds where possible | Registry failure, source build timeout, bad image tag |
| 09 offline | Offline/air-gapped helpers | Offline flags, cached assets | Offline cache/config | Safe when disabled | Missing cache, stale artifact checksum |
| 10 AMD tuning | AMD APU host tuning | AMD detection, privileges | sysctl/modprobe/GRUB/tuned changes | Avoid duplicate host config | Insufficient privilege, unsupported kernel/ROCm state |
| 11 services | Model bootstrap and stack launch | Compose set, model catalog, `.env` | Models, `models.ini`, running containers | Preserve existing models and secrets | Compose invalid, model download failure, slow health transition |
| 12 health | Service linking and post-start readiness | Running stack, expected ports, generated config | Health verdicts, Perplexica/STT setup | Extend or defer when a model swap is active | Slow model load, route unavailable, optional service disabled |
| 13 summary | User-facing completion output | Install state, hostnames, service URLs | Summary JSON, shortcuts, setup output | Regenerate safely | Bad hostname, shortcut failure, stale URL |

## Required Validation By Phase

| Changed area | Minimum validation |
|---|---|
| Phase syntax only | `bash -n installers/phases/<phase>.sh` plus `git diff --check` |
| Detection or tier logic | hardware-class tests, model selector tests, platform truth table review |
| Requirements or ports | port contract tests and `.env` schema validation |
| Docker/runtime setup | distro smoke plus relevant GPU/backend lane |
| Generated config | `python scripts/validate-generated-configs.py` |
| Compose launch | compose resolver validation and at least one install/lifecycle lane |
| Health/lifecycle | idempotent reinstall, `dream restart`, and `dream doctor` |
| Summary/setup output | install smoke plus UI/setup-card sanity when applicable |

For operational code, use [HIGH_RISK_CHANGE_MAP.md](HIGH_RISK_CHANGE_MAP.md)
and [RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) to decide whether focused
checks are enough.

## Generated Config Synchronization

If a phase writes `.env`, LiteLLM, Hermes, OpenCode, Perplexica, model routing,
or service URLs, check all writer paths:

- Linux installer;
- macOS installer;
- Windows installer;
- bootstrap upgrade;
- host-agent activation or repair;
- dashboard-api management routes.

Generated config regressions often happen when only one platform writer is
updated.

## Reinstall Contract

An idempotent reinstall should:

- keep user secrets;
- keep downloaded models when valid;
- keep runtime data under `data/`;
- refresh generated config when source contracts changed;
- recover services after compose or model health delays;
- exit non-zero only when the installed product is not recoverable or an
  explicit blocking preflight fails.

If a reinstall fails but `dream restart` and `dream doctor` pass immediately
afterward, investigate health budgets and phase handoff timing before assuming
the runtime is broken.
