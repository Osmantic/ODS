# Repository secret scanning

The repository uses Gitleaks 8.28.0 with its standard rules plus ODS's Langfuse
rules. `.gitleaks.toml` must explicitly enable `[extend] useDefault = true`;
custom rules alone replace the standard rule set. Do not globally exclude
installer generators: a real credential in one of those files must be detected.

The [CI workflow](../../.github/workflows/secret-scan.yml) runs a synthetic
coverage check before scanning Git history. That check constructs never-issued
GitHub, AWS and Langfuse markers in a temporary directory, including the three
previously excluded installer paths. It contacts no credential provider and
prints no token values. The old configuration fails the check because it does
not detect GitHub and AWS markers.

## Reviewed exceptions

The 2026-09-24 default-rule restoration found 57 additional historical findings
across the fetched branches and tags. Each was checked against its source commit.
The review covers synthetic test/evaluation strings, placeholder curl headers,
browser storage names, a password-free JDBC URL, a descriptive cache-policy
string, a public artifact hash, and empty installer key declarations matched
across a comment. Many older exceptions had the later `ods/` path even though
the original commit used older directory names. The additional remote-branch
findings include Python planning-field set names and a synthetic provenance
test input, plus duplicate vendor fixture and JDBC findings at distinct commits.

There is also an already-public historical SearXNG default key. Its exact
commit/path fingerprint is acknowledged; that does **not** make it safe for
use in an installation. Operators of legacy deployments should check privately
whether that published default remains in use and replace it with a unique
secret through their normal configuration/rotation process. This repository
review did not inspect or rotate any live deployment and does not establish
retirement of credentials previously recorded in security incidents.

[`.gitleaksignore`](../../.gitleaksignore) records exact commit/path/rule/line
fingerprints and rationale. Existing incident-related exceptions are retained;
they are not reclassified as harmless fixtures by this cleanup. An exception
is not proof of a credential's revocation. Never add broad file exclusions or
copy secret values into comments, issues or public scan reports.

## Interpreting a passing check

A passing job means the configured scanner reported no **unacknowledged**
matches in the scanned history. It does not certify that no secret exists,
inspect provider-side revocation, audit installed environments, or replace
incident response. Reports are redacted. Review new findings before adding
exceptions; a new real credential requires containment and rotation, not an
ignore entry. Keep the synthetic coverage check enabled when updating rules.
