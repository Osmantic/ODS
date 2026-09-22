# Extension expansion readiness

Updated 2026-09-21. The complete block is **not yet ready for merge**.

| Requirement | Current evidence | Status |
|---|---|---|
| 200 distinct catalog entries | Source catalog and authenticated local API both contain 200 entries and 200 unique IDs | Complete |
| Real configuration paths | 169 deployable library recipes pass the existing staged-install boundary; 31 built-in entries use their activation/native/base-compose paths | Configuration implemented |
| Preserve the cloud mascot | Current dashboard build retains the cloud assets and is served locally | Preserved |
| Do not install the catalog during development | Validation used temporary files and mocked host calls; existing application container set is unchanged | Honored |
| Catalog chat installation | Exact mentions enter the durable prerequisite coordinator; required settings use the write-only form | Implemented and covered by isolated tests |
| GitHub chat installation | Current request binds repository and recipe digest; preparation and coordinator progression are connected and locally deployed | Supported recipe path implemented |
| GitHub source projects | Commit-bound builds support upstream Dockerfiles or project-specific proposed inline Dockerfiles, with distinct provenance receipts, through preparation and staged reactivation; host pull/build separation is implemented | API/UI deployed locally and native installer script updated; application execution deferred, custom host hooks/build secrets unsupported |
| Integrate applications with project code | A current successful installation queues one real model continuation for the identified project; a saved pending record offers explicit recovery after reload with a fresh readiness read and stable request identity | Implemented and covered by isolated tests; actual model execution remains unverified |
| Windows, Linux and macOS behavior | Capability-aware recipes and native/WSL paths exist; focused POSIX tests run under WSL | All platform/hardware combinations have not been exercised |
| Credential changes on the local Windows/WSL setup | Service-configured projection refreshes only the API key before API-bound operations; 75 manager tests passed and a live read-only inspection succeeded | Implemented and locally deployed |
| Merge packaging | Combined draft [PR #6156](https://github.com/Osmantic/ODS/pull/6156) targets the existing public-beta baseline | Depends on PR #6155 merging first; current native macOS snapshot integrated, final CI pending |

The latest combined isolated API run passed 338 tests covering library staging,
source recipes, GitHub evidence, request binding and recipe publication.
Additional focused runs passed 36 host installation tests, four proposal-tool
tests and 26 Portal installation/progress tests. Project association requires
a successful current installation command; opening saved history does not
submit project-association mutations.

These checks establish only the behavior they exercise; they do not prove that
every upstream application runs on every GPU or operating system. Actual fleet
and application execution remain deferred at the user's request.

No extension application images were pulled or started to establish these
results. Runtime HTTP checks confirmed the Portal, catalog, request route and
an actual recipe guidance response without conversing with the model.

## macOS dependency

Merge [PR #6155](https://github.com/Osmantic/ODS/pull/6155) before this PR.
The extension branch incorporates its `6d36b3fde02935eca21db228afa4cf2634f0c351`
snapshot, resolves the shared installer extraction, and retains the proposal
tool in both onboarding and runtime allowlists. GitHub repository/proposal
clients use the native manager socket on macOS. This does not qualify Intel
Macs or additional hardware beyond the native PR's stated support.

Compatibility checks passed: 296 shared installer checks, 75 manager tests,
six proposal transport tests and 23 onboarding/runtime-budget tests under
Linux/WSL with isolated fixtures. Actual Mac execution remains for the owner.
The local Portal and catalog respond with HTTP 200 and 200 catalog entries.

Catalog environment declarations now include the extension configuration keys;
all 51 environment validation checks passed, including port range checks.

## Complete-catalog audit follow-up

The generated catalog still has 200 unique IDs and names. The strict audit of
all 203 source definitions (including three intentionally excluded definitions)
now reports zero errors and warnings. Legacy Aider preserves the upstream
executable, uses the current ODS gateway model and runs only `--version` during
installation. Piper declares its Wyoming/native-health contract. TCP services
with HTTP startup checks disabled no longer receive a false `cli_installed`
status. An ambiguous legacy Dify alias was removed.

All 178 recipe-staging/audit tests and five focused CLI/TCP status regressions
passed. The Fooocus immutable image is now recorded at its actual built-in
path in the dependency lock; dependency-pin checks pass. These changes are
applied to the local catalog/API without installing any catalog applications.
