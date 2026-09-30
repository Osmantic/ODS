#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PHASE="$ROOT/installers/phases/07-devtools.sh"
CORE="$ROOT/install-core.sh"

dry_plan() {
    (
        SCRIPT_DIR="$ROOT"
        DRY_RUN=true
        ENABLE_DEVTOOLS="$1"
        ENABLE_OPENCODE="${2:-false}"
        ods_progress() { :; }
        log() { printf '%s\n' "$*"; }
        source "$PHASE"
    )
}

off="$(dry_plan false)"
on="$(dry_plan true)"
opencode_only="$(dry_plan false true)"
[[ "$off" == *'Developer CLIs disabled; existing binaries would be preserved'* ]]
[[ "$off" != *'Would install AI developer tools'* ]]
[[ "$on" == *'Would install AI developer tools (Claude Code and Codex CLI)'* ]]
[[ "$opencode_only" == *'Would install and configure the optional OpenCode browser IDE'* ]]
[[ "$opencode_only" != *'Would install AI developer tools'* ]]

grep -Fq -- '--with-devtools) ENABLE_DEVTOOLS=true' "$CORE"
grep -Fq -- '--no-devtools) ENABLE_DEVTOOLS=false' "$CORE"
grep -Fq 'ENABLE_DEVTOOLS=false' "$CORE"
grep -Fq 'external_llm_env_value "$INSTALL_DIR/.env" ENABLE_DEVTOOLS' "$CORE"
grep -Fq 'ENABLE_DEVTOOLS=${ENABLE_DEVTOOLS:-false}' "$ROOT/installers/phases/06-directories.sh"
grep -Fq 'ENABLE_DEVTOOLS=false' "$ROOT/.env.example"
grep -Fq 'Developer CLI installation disabled; existing Claude Code and Codex binaries preserved' "$PHASE"

# Host agent and mDNS must remain after the opt-in block in the same phase.
grep -Fq 'ODS Host Agent (extension lifecycle management)' "$PHASE"
grep -Fq 'ODS mDNS' "$PHASE"

printf '[PASS] Linux developer CLIs require an explicit opt-in and preserve installed tools\n'
