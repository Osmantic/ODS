# ODS Security Audit Status

- **Original audit date:** 2026-03-08
- **Original analyst:** latentcollapse
- **Status review:** 2026-05-21
- **Scope:** ODS public repository under its former GitHub organization at audit time; now `Osmantic/ODS`. Local clone only. No live infrastructure was touched.
- **Current operator guide:** [`ods/SECURITY.md`](ods/SECURITY.md)

This document tracks the remediation status of the March 2026 static security
audit. It is not a live list of active vulnerabilities. Treat a finding as
currently active only when its status says `Open` or `Needs confirmation`.

The original audit used gitleaks 8.x, bandit 1.9.4, semgrep auto config,
shellcheck, and manual review. The status review below was based on the current
repository tree, targeted regression tests, and security-relevant docs.

## Status Key

| Status | Meaning |
|--------|---------|
| `Remediated in tree` | Current source or config contains the mitigation, with file or test evidence. |
| `Resolved / operator-confirmed` | The maintained source tree no longer contains the vulnerable behavior, and the remaining external action has been confirmed by maintainers or a private incident record. |
| `Mitigated / accepted local risk` | The behavior changed, but a local-only or operator-controlled residual risk remains by design. |
| `Needs confirmation` | Repository state cannot prove the full remediation, usually because it requires an external secret rotation or live-site change. |
| `External / out of repo` | The finding concerns infrastructure or web properties not represented in this repository. |

## Current Summary

| Original severity | Original count | Current status |
|-------------------|----------------|----------------|
| Critical | 1 | Resolved by operator confirmation: code artifact is historical/removed from the maintained product tree, and the exposed LiveKit credentials were already retired when they leaked. |
| High | 3 | All three have current code/config mitigations and regression evidence. |
| Medium | 5 | Four are remediated in tree; one is mitigated by HTTPS-aware behavior with local HTTP accepted. |
| Low | 2 | One is remediated in tree; one concerns the external marketing site and is outside repository verification. |

## Findings Status

| ID | Original finding | Current status | Evidence / receipt | Remaining action |
|----|------------------|----------------|--------------------|------------------|
| C1 | Likely real LiveKit credentials committed to a historical voice-agent token server | Resolved / operator-confirmed | The file is historical and removed from the maintained product tree. Maintainers confirmed the exposed LiveKit credentials had already been retired when they leaked; the private incident record remains the authoritative evidence because git cannot prove external credential state. | Keep the private incident record for auditability. Do not publish unredacted credentials. |
| H1 | SearXNG shipped a static shared `secret_key` | Remediated in tree | [`ods/config/searxng/settings.yml`](ods/config/searxng/settings.yml) now contains an installer placeholder; Linux and Windows installers generate `SEARXNG_SECRET`; [`ods/extensions/services/searxng/compose.yaml`](ods/extensions/services/searxng/compose.yaml) requires it. | Keep generated secrets out of committed config and support bundles. |
| H2 | Installer used `eval` on helper script output | Remediated in tree | [`ods/lib/safe-env.sh`](ods/lib/safe-env.sh) provides non-evaluating parsers; maintained installer paths call `load_env_from_output`; [`ods/tests/test-safe-env.sh`](ods/tests/test-safe-env.sh) verifies command substitutions are not executed. | Keep docs and future scripts on `safe-env.sh`; avoid reintroducing `eval "$(...)"` examples. |
| H3 | OpenClaw gateway combined disabled device auth with LAN binding | Remediated in tree | OpenClaw is deprecated and optional in [`ods/extensions/services/openclaw/manifest.yaml`](ods/extensions/services/openclaw/manifest.yaml); compose requires `OPENCLAW_TOKEN`; static configs set `dangerouslyDisableDeviceAuth` false; [`ods/tests/contracts/test-network-exposure-contracts.py`](ods/tests/contracts/test-network-exposure-contracts.py) keeps OpenClaw opt-in and token-gated. | Keep OpenClaw legacy-only; default users should use Hermes plus `hermes-proxy`. |
| M1 | Token-spy SQL migration interpolated identifiers | Remediated in tree | [`ods/extensions/services/token-spy/db.py`](ods/extensions/services/token-spy/db.py) uses `ALLOWED_COLUMNS` and a safe SQL identifier regex before `ALTER TABLE`. | Preserve the allowlist if columns are made dynamic later. |
| M2 | Dashboard and token-spy containers ran as root | Remediated in tree | [`ods/extensions/services/dashboard/Dockerfile`](ods/extensions/services/dashboard/Dockerfile) and [`ods/extensions/services/token-spy/Dockerfile`](ods/extensions/services/token-spy/Dockerfile) create and run as non-root users. | Keep new service Dockerfiles covered by extension audit and review. |
| M3 | Dashboard nginx config had H2C smuggling conditions | Remediated in tree | [`ods/extensions/services/dashboard/nginx.conf`](ods/extensions/services/dashboard/nginx.conf) sets `proxy_set_header Connection "close"` on the API proxy path. | If WebSocket upgrade support is added to that path, re-review the proxy headers. |
| M4 | Voice agent defaulted to unencrypted `ws://` | Mitigated / accepted local risk | [`ods/extensions/services/dashboard/src/hooks/useVoiceAgent.js`](ods/extensions/services/dashboard/src/hooks/useVoiceAgent.js) now derives `wss:` when the dashboard is served over HTTPS and `ws:` for local HTTP. [`ods/SECURITY.md`](ods/SECURITY.md) recommends TLS or VPN for network exposure. | Plain HTTP on localhost/LAN remains cleartext by design; use TLS or Tailscale/WireGuard for sensitive shared deployments. |
| M5 | `local` was used outside function scope in installer service phase | Remediated in tree | Current [`ods/installers/phases/11-services.sh`](ods/installers/phases/11-services.sh) keeps `local` declarations inside functions. | Continue running shellcheck or installer contract tests on shell changes. |
| L1 | CDN-loaded dashboard assets lacked Subresource Integrity | Remediated in tree | [`ods/extensions/services/dashboard/public/agents.html`](ods/extensions/services/dashboard/public/agents.html) and [`ods/extensions/services/dashboard/templates/index.html`](ods/extensions/services/dashboard/templates/index.html) include `integrity` and `crossorigin` on CDN assets. | Keep SRI hashes updated when CDN versions change. |
| L2 | `ods.ai` marketing site missed common security headers | External / out of repo | This repository does not contain the marketing-site hosting config, CDN config, or deployed headers. | Track separately with the website host/CDN owner; re-check with a live header scan before claiming fixed. |

## Current Security Receipts

These files and tests are the strongest in-repository evidence that the project
has moved beyond the original audit findings:

| Area | Receipt |
|------|---------|
| Operator posture | [`ods/SECURITY.md`](ods/SECURITY.md) documents localhost defaults, LAN tradeoffs, host-agent binding, TLS/VPN guidance, API gateway auth, and disclosure. |
| Network exposure policy | [`ods/config/network-exposure-policy.json`](ods/config/network-exposure-policy.json) labels every host-facing or host-networked service with risk, LAN exposure, and auth expectations. |
| Exposure contracts | [`ods/tests/contracts/test-network-exposure-contracts.py`](ods/tests/contracts/test-network-exposure-contracts.py) enforces Hermes internal-only behavior, `hermes-proxy` auth gating, OpenClaw deprecation/token gating, and LiteLLM auth. |
| Safe env parsing | [`ods/lib/safe-env.sh`](ods/lib/safe-env.sh) and [`ods/tests/test-safe-env.sh`](ods/tests/test-safe-env.sh) replace shell `eval` ingestion for helper output and `.env` loading. |
| Secret checks | [`ods/tests/test-secret-security.sh`](ods/tests/test-secret-security.sh) scans for hardcoded secrets, auth patterns, `.gitignore` coverage, and the token-spy SQL guard. |
| Extension hardening | [`ods/scripts/audit-extensions.py`](ods/scripts/audit-extensions.py) rejects unsafe compose patterns for bundled and user extensions. |
| Support bundle redaction | [`ods/docs/SUPPORT-BUNDLE.md`](ods/docs/SUPPORT-BUNDLE.md) documents redaction expectations before users share diagnostics. |

## Residual Risks To Keep Visible

- External credential state cannot be proven from git. For historical credential
  findings such as the LiveKit incident, keep a private incident record that
  documents retirement or rotation timing without publishing secrets.
- `--lan` and `BIND_ADDRESS=0.0.0.0` are operator-controlled exposure choices.
  They are useful for headless devices and private networks, but they should be
  paired with firewall rules, TLS, or a VPN.
- ODS is still local-first. Public internet deployments need an
  additional reverse proxy, TLS, rate limiting, and service-specific auth review.
- Local HTTP voice traffic remains unencrypted unless HTTPS or a VPN is placed
  in front of it.
- External properties such as `ods.ai` need their own security receipt
  trail because this repository cannot validate deployed headers or CDN policy.

## Verification Commands

Use these from the repository root when updating this status page:

```bash
python ods/tests/contracts/test-network-exposure-contracts.py
bash ods/tests/test-safe-env.sh
bash ods/tests/test-openclaw-device-auth-default.sh
python ods/scripts/audit-extensions.py --project-dir ods
git diff --check
```

If a command is skipped because a local dependency is unavailable, note that in
the PR body rather than silently treating the receipt as current.

## Maintainer Checklist For Future Audit Updates

- Update the `Status review` date.
- Keep original finding IDs stable so older discussions remain searchable.
- Link to the code, config, test, or PR that proves each remediation.
- Separate code remediation from external actions such as credential rotation.
- Keep active risks in present tense and historical risks in past tense.
- Prefer adding regression tests before marking a finding `Remediated in tree`.
