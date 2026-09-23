#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

usage() {
  echo "Usage: scripts/run-upstream-assurance.sh --evidence ABSOLUTE_PATH [--passes 2] [--pressure-iterations 2]" >&2
  exit 2
}

evidence=""
passes=2
pressure_iterations=2
while [[ $# -gt 0 ]]; do
  case "$1" in
    --evidence) evidence=${2:-}; shift 2 ;;
    --passes) passes=${2:-}; shift 2 ;;
    --pressure-iterations) pressure_iterations=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done
[[ "$evidence" == /* && "$evidence" != / ]] || usage
[[ "$passes" =~ ^[2-9][0-9]*$ ]] || { echo "At least two consecutive passes are required" >&2; exit 2; }
[[ "$pressure_iterations" =~ ^[1-9][0-9]*$ ]] || usage
case "$evidence/" in "$ROOT/"*) echo "Evidence must be outside the source repository" >&2; exit 2 ;; esac
[[ ! -e "$evidence" ]] || { echo "Evidence path already exists" >&2; exit 1; }

cd "$ROOT"
[[ -z $(git status --porcelain=v1 --untracked-files=all) ]] || { echo "Assurance requires a clean worktree" >&2; exit 1; }
commit=$(git rev-parse HEAD)
tree=$(git rev-parse 'HEAD^{tree}')
manifest_sha256=$(python3 -c 'import hashlib,json,sys; value=json.load(open(sys.argv[1], encoding="utf-8")); print(hashlib.sha256((json.dumps(value,sort_keys=True,separators=(",",":"))+"\n").encode()).hexdigest())' RELEASE-MANIFEST.json)
catalog_sha256=$(sha256sum security-evals/assurance/cases.json | awk '{print $1}')
binding=$(printf '{"attackCatalogSha256":"%s","manifestSha256":"%s","sourceCommit":"%s","sourceTree":"%s"}' "$catalog_sha256" "$manifest_sha256" "$commit" "$tree")

install -d -m 700 "$evidence"
python3 security-evals/assurance/manifest.py "$evidence/source-manifest.json"
set +e
python3 security-evals/assurance/audit_source.py --history > "$evidence/source-audit.json"
source_audit_rc=$?
set -e
chmod 600 "$evidence/source-manifest.json" "$evidence/source-audit.json"
[[ "$source_audit_rc" -le 1 ]] || { echo "Source assurance audit failed" >&2; exit "$source_audit_rc"; }
jq -e '
  .schemaVersion == 2 and .secretValuesEmitted == false and
  .history.scanned == true and
  (.current.findings | length) == 0 and
  (.current.skippedLargeFiles | length) == 0 and
  (.dependencies.findings | length) == 0 and
  (.history.findings | length) == 0 and
  (.history.skippedLargeObjects | length) == 0 and
  (.policyViolations | length) == 0 and
  .reviewedPolicy.schemaVersion == 2 and
  .reviewedPolicy.repository == "Osmantic/Pixel" and
  (.reviewedPolicy.sha256 | test("^[0-9a-f]{64}$")) and
  .reviewedPolicy.reviewedBlobCount > 0 and
  .reviewedPolicy.acknowledgedBlobCount > 0 and
  (.history.acknowledgedFixtures | length) == .reviewedPolicy.acknowledgedBlobCount
' "$evidence/source-audit.json" >/dev/null
python3 security-evals/assurance/audit_remote_refs.py --remote origin > "$evidence/remote-ref-audit.json"
chmod 600 "$evidence/remote-ref-audit.json"

for ((pass = 1; pass <= passes; pass++)); do
  [[ $(git rev-parse HEAD) == "$commit" && $(git rev-parse 'HEAD^{tree}') == "$tree" ]] || {
    echo "Source identity changed during assurance" >&2
    exit 1
  }
  test_log="$evidence/pass-${pass}-test.log"
  pressure_log="$evidence/pass-${pass}-pressure.log"
  pressure_report="$evidence/pass-${pass}-pressure.jsonl"
  printf 'EVIDENCE_BINDING %s\n' "$binding" > "$test_log"
  printf 'EVIDENCE_BINDING %s\n' "$binding" > "$pressure_log"
  chmod 600 "$test_log" "$pressure_log"
  ./pixel test 2>&1 | tee -a "$test_log"
  PIXEL_PRESSURE_REPORT="$pressure_report" ./pixel pressure "$pressure_iterations" 2>&1 | tee -a "$pressure_log"
  chmod 600 "$pressure_report"
  [[ $(wc -l < "$pressure_report") -eq $pressure_iterations ]]
  jq -e -s --argjson count "$pressure_iterations" 'length == $count and all(.status == "passed")' "$pressure_report" >/dev/null
  bash scripts/check-no-secrets.sh >> "$test_log" 2>&1
done

[[ -z $(git status --porcelain=v1 --untracked-files=all) ]] || { echo "Assurance mutated the source worktree" >&2; exit 1; }
bash scripts/check-no-secrets.sh "$evidence" > "$evidence/evidence-secret-scan.log" 2>&1
chmod 600 "$evidence/evidence-secret-scan.log"
python3 - "$evidence" "$commit" "$tree" "$manifest_sha256" "$catalog_sha256" "$passes" "$pressure_iterations" <<'PY'
import hashlib, json, pathlib, sys
root = pathlib.Path(sys.argv[1])
commit, tree, manifest_sha, catalog_sha = sys.argv[2:6]
passes, pressure_iterations = int(sys.argv[6]), int(sys.argv[7])
records = []
for number in range(1, passes + 1):
    test_log = root / f"pass-{number}-test.log"
    pressure_log = root / f"pass-{number}-pressure.log"
    pressure_report = root / f"pass-{number}-pressure.jsonl"
    records.append({
        "pass": number,
        "status": "pass",
        "testLogSha256": hashlib.sha256(test_log.read_bytes()).hexdigest(),
        "pressureLogSha256": hashlib.sha256(pressure_log.read_bytes()).hexdigest(),
        "pressureReportSha256": hashlib.sha256(pressure_report.read_bytes()).hexdigest(),
        "pressureIterations": pressure_iterations,
    })
source_audit = json.loads((root / "source-audit.json").read_text(encoding="utf-8"))
remote_ref_audit = json.loads((root / "remote-ref-audit.json").read_text(encoding="utf-8"))
summary = {
    "schemaVersion": 1,
    "operation": "pixel-full-assurance",
    "status": "pass",
    "sourceCommit": commit,
    "sourceTree": tree,
    "releaseManifestSha256": manifest_sha,
    "attackCatalogSha256": catalog_sha,
    "reviewedPolicySha256": source_audit["reviewedPolicy"]["sha256"],
    "acknowledgedFixtureCount": len(source_audit["history"]["acknowledgedFixtures"]),
    "consecutivePasses": passes,
    "passes": records,
    "remoteRefAudit": {
        "releaseGateStatus": remote_ref_audit["releaseGate"]["status"],
        "activeRefCount": len(remote_ref_audit["releaseGate"]["refs"]),
        "legacyRefCount": len(remote_ref_audit["legacyHistory"]["refs"]),
        "legacyFindingCount": len(remote_ref_audit["legacyHistory"]["history"]["findings"]),
        "legacySkippedObjectCount": len(remote_ref_audit["legacyHistory"]["history"]["skippedLargeObjects"]),
    },
    "artifacts": {
        name: {
            "bytes": (root / name).stat().st_size,
            "sha256": hashlib.sha256((root / name).read_bytes()).hexdigest(),
        }
        for name in (
            "source-manifest.json",
            "source-audit.json",
            "remote-ref-audit.json",
            "evidence-secret-scan.log",
        )
    },
}
path = root / "assurance-summary.json"
path.write_text(json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
path.chmod(0o600)
print(json.dumps({"status": "pass", "evidence": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}, sort_keys=True))
PY
