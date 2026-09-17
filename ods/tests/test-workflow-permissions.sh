#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORKFLOW_DIR="$ROOT_DIR/.github/workflows"
checked=0

fail() {
    echo "FAIL: $*" >&2
    exit 1
}

job_block() {
    local workflow="$1"
    local job="$2"

    awk -v header="  ${job}:" '
        $0 == header { in_job = 1 }
        in_job && $0 ~ /^  [[:alnum:]_-]+:$/ && $0 != header { exit }
        in_job { print }
    ' "$workflow"
}

for workflow in "$WORKFLOW_DIR"/*.yml; do
    [[ -f "$workflow" ]] || continue
    grep -q '^permissions:$' "$workflow" \
        || fail "$(basename "$workflow") has no workflow-level permissions default"
    ! grep -qE '^permissions:[[:space:]]*write-all|^[[:space:]]+actions:[[:space:]]+write$' "$workflow" \
        || fail "$(basename "$workflow") grants an overbroad workflow-level permission"
    checked=$((checked + 1))
done

[[ "$checked" -gt 0 ]] || fail "no workflow files found"

issue_to_pr="$WORKFLOW_DIR/issue-to-pr.yml"
! grep -q '^  pull-requests: read$' "$issue_to_pr" \
    || fail "issue-to-pr grants pull-request read access to every job"
validate_block="$(job_block "$issue_to_pr" validate)"
grep -q '^      pull-requests: read$' <<< "$validate_block" \
    || fail "issue-to-pr validation needs pull-request read access"
grep -q '^      issues: write$' "$issue_to_pr" \
    || fail "issue-to-pr creation job needs issue comment access"

nightly_review="$WORKFLOW_DIR/nightly-code-review.yml"
! grep -q '^  pull-requests: read$' "$nightly_review" \
    || fail "nightly code review grants pull-request read access to every job"
preflight_block="$(job_block "$nightly_review" preflight)"
grep -q '^      pull-requests: read$' <<< "$preflight_block" \
    || fail "nightly code review preflight needs pull-request read access"

nightly_docs="$WORKFLOW_DIR/nightly-docs-update.yml"
! grep -q '^  pull-requests: read$' "$nightly_docs" \
    || fail "nightly docs grants pull-request read access to every job"
detect_block="$(job_block "$nightly_docs" detect-changes)"
grep -q '^      pull-requests: read$' <<< "$detect_block" \
    || fail "nightly docs detection needs pull-request read access"
grep -q '^      contents: write$' "$nightly_docs" \
    || fail "nightly docs PR job needs scoped contents write access"
grep -q '^      pull-requests: write$' "$nightly_docs" \
    || fail "nightly docs PR job needs scoped pull-request write access"

scanner="$WORKFLOW_DIR/autonomous-code-scanner.yml"
create_prs_block="$(job_block "$scanner" create-prs)"
grep -q '^      actions: read$' <<< "$create_prs_block" \
    || fail "autonomous scanner PR job needs action artifact read access"

echo "test-workflow-permissions: $checked workflows checked"
