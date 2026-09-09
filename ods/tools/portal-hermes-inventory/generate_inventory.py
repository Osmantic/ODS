#!/usr/bin/env python3
"""
Deterministic, fail-closed predecessor preservation inventory generator.

Enumerates every changed path from source PRs, classifies into subsystems,
assigns dispositions, and produces the canonical inventory JSON.

Each record carries a non-empty rationale (mechanical disposition routing hypothesis).
Per-PR source_delta_sha256 is computed from sorted change_type<TAB>path records.
Expected hard-coded base/head/count/delta digests are validated.

Usage:
    python3 generate_inventory.py --base-dir /path/to/repo --out inventory.json

The output inventory is sorted deterministically and includes a canonical SHA-256.
All dispositions are mechanical routing hypotheses pending integration review.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter

# ─── Hard-coded PR references (fail-closed) ───────────────────────────

PR_DEFS = {
    "pr-3385": {
        "base_sha": "21f4b3a64dd2a2fac1163f446806091c25b6b814",
        "head_sha": "53bf63fa256b6bed27764cace170d45aa43c75d1",
        "expected_changed_path_count": 283,
        "expected_source_delta_sha256": "6baaa5dd2bae01b297b75efc387d0dea88c3bb7c9eb7298a6562087e9a3e8c0b",
    },
    "pr-3818": {
        "base_sha": "58a2d544b8752cee2b42d54001100d9ef2e89ae0",
        "head_sha": "90e74b01c597bec13b12955ad04acb1f0bf68b16",
        "expected_changed_path_count": 144,
        "expected_source_delta_sha256": "e2f1f236e6a9e3fdfb4650ac58a0ac627b997e80e36ff9d496c07d3da39a9b22",
    },
}

# ─── Subsystem taxonomy (canonical, ordered) ───────────────────────────

SUBSYSTEMS = {
    "core-runtime": "core/runtime replacement",
    "ingress-edge": "ingress/edge",
    "model-routing": "model/fleet routing",
    "providers": "providers/connections",
    "access-scopes": "access/scopes",
    "leases-approvals": "leases/approvals",
    "source-broker": "source broker",
    "operations": "operations/frontier",
    "previews": "previews/artifacts/extensions",
    "dashboard-api": "dashboard/API",
    "installer": "installer/migration/rollback/uninstall",
    "observability": "observability/receipts/custody",
    "tests-docs": "tests/CI/docs",
    "other": "another narrowly justified subsystem",
}


# ─── Subsystem routing rules (mechanical classification) ───────────────

def is_test_path(path: str) -> bool:
    """Return true for repository test paths before capability routing."""
    segments = path.lower().split("/")
    basename = segments[-1]
    return (
        "tests" in segments
        or "__tests__" in segments
        or basename.startswith("test_")
        or ".test." in basename
        or basename.endswith("_test.py")
        or basename.endswith(".bats")
    )


def classify_subsystem(path: str) -> str:
    """Route a path to a subsystem using mechanical prefix/content rules."""
    p = path

    # Tests retain their behavioral-contract identity and must not be stolen by
    # capability keywords such as handoff, scope, preview, or approval.
    if is_test_path(p):
        return "tests-docs"

    # Capability routes must precede the broad pixel-agent/provider catch-alls.
    if any(token in p for token in (
        "bin/source_broker/", "bin/ods-source-broker", "source-broker/",
        "source_broker_", "_source_broker",
    )):
        return "source-broker"

    if any(token in p for token in (
        "workspace_preview", "workspace-preview",
        "artifact_promoter", "artifact-promoter", "evidence-artifact",
        "extension_manager", "extension-manager", "extension_search",
        "download-promote",
    )):
        return "previews"

    if any(token in p for token in (
        "system_observe", "host-observe", "ods_token_spy_callback",
        "dashboard-api/service_health_dns",
    )):
        return "observability"

    if any(token in p for token in (
        "bin/pixel_access", "pixel_provider/scopes.py",
        "pixel-agent/host/access_mode_", "pixel-agent/host/pixel_access_mode.py",
        "pixel-agent/host/ods-pixel-access.service",
        "pixel-agent/plugin/access-runtime.mjs",
        "dashboard-api/routers/pixel_scopes.py",
    )):
        return "access-scopes"

    if any(token in p for token in (
        "bin/ods-pixel-approve", "bin/ods-pixel-route-lease",
        "pixel_provider/lease_claim.py", "pixel_provider/handoff_approvals.py",
        "pixel_provider/handoff_worker.py",
        "dashboard-api/routers/pixel_handoff.py",
        "pixel-agent/plugin/handoff-approval.mjs",
        "pixel-agent/plugin/handoff-owner-worker.mjs",
        "pixel-agent/plugin/provider-lease-worker.mjs",
    )):
        return "leases-approvals"

    if any(token in p for token in (
        "pixel-agent/host/pixel-ingress.service",
        "pixel-agent/host/pixel_ingress.mjs",
    )):
        return "ingress-edge"

    if "pixel-agent/host/pixel-ops-broker" in p:
        return "operations"

    # PR 3385 paths (core pixel-agent, edge, dashboard integration)
    if "extensions/services/pixel-agent/host" in p:
        return "core-runtime"
    if "extensions/services/pixel-agent/plugin" in p:
        return "core-runtime"
    if "extensions/services/pixel-agent/manifest.yaml" in p:
        return "core-runtime"
    if "extensions/services/pixel-agent/README.md" in p:
        return "core-runtime"
    if "extensions/services/pixel-agent/FULL-ACCESS.md" in p:
        return "tests-docs"
    if "extensions/services/pixel-agent/tests/" in p:
        return "tests-docs"

    if "extensions/services/pixel-edge" in p:
        return "ingress-edge"

    # Dashboard frontend components
    if "extensions/services/dashboard/src" in p:
        return "dashboard-api"

    # Dashboard API (backend)
    if "extensions/services/dashboard-api/routers/pixel" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/pixel_runtime_state" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/service_health_dns" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/tests/test_pixel" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_service_health_dns" in p:
        return "tests-docs"

    # Bin tools
    if "bin/pixel_access" in p:
        return "access-scopes"
    if "bin/ods-pixel-approve" in p:
        return "leases-approvals"

    # Model routing
    if "bin/model_switchboard" in p:
        return "model-routing"
    if "extensions/services/model-router" in p:
        return "model-routing"
    if "config/model-library.json" in p:
        return "model-routing"
    if "config/litellm" in p:
        return "model-routing"

    # Scripts (model selection, preservation)
    if "scripts/preserve-active-model" in p:
        return "model-routing"
    if "scripts/select-external-lemonade-model" in p:
        return "model-routing"

    # Installer
    if "installers" in p:
        return "installer"
    if "lib/pixel-uninstall" in p:
        return "installer"
    if "ods-uninstall" in p and "test" not in p:
        return "installer"
    if "install-core" in p:
        return "installer"

    # Docker/compose
    if "docker-compose.base" in p:
        return "operations"

    # Config
    if "config/dependency-lock" in p:
        return "operations"
    if "config/extensions-catalog" in p:
        return "operations"
    if "config/network-exposure-policy" in p:
        return "operations"
    if "config/searxng" in p:
        return "operations"

    # .env
    if ".env" in p:
        return "operations"

    # Extensions schema
    if "extensions/library/schema" in p or "extensions/schema" in p:
        return "operations"

    # CI
    if ".github/workflows" in p:
        return "tests-docs"

    # Docs
    if "docs/pixel" in p:
        return "tests-docs"
    if "docs/PIXEL.md" in p:
        return "tests-docs"
    if "docs/HOW-ODS" in p or "docs/MODEL" in p or "docs/POST-INSTALL" in p:
        return "tests-docs"
    if "docs/README" in p or "docs/SUPPORT-MATRIX" in p:
        return "tests-docs"
    if "docs/MODE-SWITCH" in p:
        return "model-routing"

    # Dashboard API config/helpers
    if "extensions/services/dashboard-api/config" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/context_policy" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/helpers" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/host_agent_client" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/main" in p and "test" not in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/models" in p and "test" not in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/performance_oracle" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/routers/extensions" in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/routers/models" in p and "test" not in p:
        return "dashboard-api"
    if "extensions/services/dashboard-api/routers/remote_provider_status" in p:
        return "dashboard-api"

    # Dashboard tests (non-pixel-specific)
    if "extensions/services/dashboard-api/tests/test_config" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_csrf" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_extensions" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_host_agent" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_main" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_model_activate" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_models" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_model_state" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_performance" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_remote_provider" in p:
        return "tests-docs"

    # Dashboard frontend (non-pixel)
    if "extensions/services/dashboard/Dockerfile" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/entrypoint" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/nginx" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/package" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/hooks" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/main.jsx" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/pages/Models" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/pages/RemoteProvider" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/pages/Settings" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/plugins" in p:
        return "dashboard-api"

    # LiteLLM service
    if "extensions/services/litellm" in p and "config/litellm" not in p:
        return "model-routing"

    # Llama-server
    if "extensions/services/llama-server" in p:
        return "model-routing"

    # OpenClaw extension service
    if "extensions/services/openclaw" in p:
        return "core-runtime"

    # Perplexica
    if "extensions/services/perplexica" in p:
        return "model-routing"

    # Host agent
    if "bin/ods-host-agent" in p:
        return "core-runtime"

    # Remote provider lifecycle
    if "bin/remote_provider" in p:
        return "providers"

    # Lib scripts
    if "lib/rootless" in p:
        return "installer"
    if "lib/safe-env" in p:
        return "operations"
    if "lib/service-registry" in p:
        return "operations"

    # Scripts
    if "scripts/audit-extensions" in p:
        return "operations"
    if "scripts/bootstrap-upgrade" in p:
        return "installer"
    if "scripts/detect-hardware" in p:
        return "installer"
    if "scripts/generate-extensions-catalog" in p:
        return "operations"
    if "scripts/render-runtime-configs" in p:
        return "operations"
    if "scripts/repair" in p:
        return "operations"
    if "scripts/resolve-compose" in p:
        return "operations"
    if "scripts/select-model" in p:
        return "model-routing"

    # Root README / QUICKSTART
    if p == "README.md" or (p.endswith("/README.md") and "docs/pixel" not in p):
        return "tests-docs"
    if "QUICKSTART" in p:
        return "tests-docs"

    # ODS CLI
    if "ods-cli" in p and "test" not in p:
        return "installer"

    # Top-level tests
    if p.startswith("ods/tests/") or p.startswith("tests/"):
        return "tests-docs"

    # PR 3815 paths (pixel_provider, advice, connections, scopes, sharing)
    if "bin/pixel_provider/" in p:
        return "providers"
    if "bin/ods-pixel-advice" in p:
        return "providers"
    if "bin/ods-pixel-connect" in p:
        return "providers"
    if "bin/ods-pixel-route-lease" in p:
        return "leases-approvals"

    # Dashboard API for PR 3818
    if "extensions/services/dashboard-api/pixel_provider_public" in p:
        return "providers"
    if "extensions/services/dashboard-api/pixel_sharing_public" in p:
        return "providers"
    if "extensions/services/dashboard-api/routers/pixel_advice" in p:
        return "providers"
    if "extensions/services/dashboard-api/routers/pixel_handoff" in p:
        return "leases-approvals"
    if "extensions/services/dashboard-api/routers/pixel_providers" in p:
        return "providers"
    if "extensions/services/dashboard-api/routers/pixel_scopes" in p:
        return "access-scopes"
    if "extensions/services/dashboard-api/routers/pixel_sharing" in p:
        return "providers"
    if "extensions/services/dashboard-api/tests/test_pixel_advice" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_pixel_handoff" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_pixel_providers" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_pixel_scopes" in p:
        return "tests-docs"
    if "extensions/services/dashboard-api/tests/test_pixel_sharing" in p:
        return "tests-docs"

    # Dashboard frontend for PR 3818
    if "extensions/services/dashboard/src/components/PixelAdvice" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/components/PixelHandoff" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/components/PixelProvider" in p and "Settings" not in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/components/settings/pixelProvider" in p:
        return "dashboard-api"
    if "extensions/services/dashboard/src/components/settings/pixelSharing" in p or \
       "extensions/services/dashboard/src/components/settings/PixelSharing" in p:
        return "dashboard-api"

    # Pixel agent plugin for PR 3818
    if "extensions/services/pixel-agent/plugin/handoff" in p:
        return "leases-approvals"
    if "extensions/services/pixel-agent/plugin/provider" in p:
        return "providers"

    # Pixel agent docs for PR 3818
    if "extensions/services/pixel-agent/PROVIDER" in p:
        return "tests-docs"

    # Pixel agent runtime-source
    if "extensions/services/pixel-agent/runtime-source" in p:
        return "core-runtime"

    # Pixel agent tests/fixtures for PR 3818
    if "extensions/services/pixel-agent/tests/fixtures" in p:
        return "tests-docs"
    if "extensions/services/pixel-agent/tests/handoff" in p:
        return "tests-docs"
    if "extensions/services/pixel-agent/tests/provider" in p:
        return "tests-docs"

    # Pixel inference service
    if "extensions/services/pixel-inference" in p:
        return "providers"

    # Tests for PR 3818
    if "tests/pixel_inference/" in p:
        return "tests-docs"
    if "tests/test_pixel_activity" in p:
        return "tests-docs"
    if "tests/test_pixel_provider" in p:
        return "tests-docs"

    # .gitattributes
    if ".gitattributes" in p:
        return "operations"

    return "other"


def classify_disposition(path: str, subsystem: str, change_type: str) -> tuple:
    """
    Assign disposition. Returns (disposition, classification_method, rationale).
    Methods: 'mechanical' (auto-routed) or 'manual' (inspected).
    Rationale is a non-empty description of the routing hypothesis.
    """
    p = path

    # Preserve tests as behavioral contracts. Tests named for OpenClaw still
    # need a Hermes-native rewrite; other capability names do not imply one.
    if subsystem == "tests-docs":
        if "openclaw" in p.lower():
            return ("replace-openclaw-coupling", "mechanical",
                    "Test or documentation explicitly targets OpenClaw and requires a Hermes-native equivalent")
        return ("retain-semantics", "mechanical",
                "Test or documentation captures a behavioral contract to retain")

    # OpenClaw-coupled runtime files
    if "openclaw" in p.lower():
        return ("replace-openclaw-coupling", "mechanical",
                "Contains OpenClaw coupling requiring replacement in Hermes")

    # Pixel-agent host/plugin code belongs to the predecessor runtime even when
    # its capability is routed into previews, observability, access, or leases.
    if "extensions/services/pixel-agent/host/" in p or \
       "extensions/services/pixel-agent/plugin/" in p:
        return ("replace-openclaw-coupling", "mechanical",
                "Pixel-agent runtime coupling requires a Hermes-native equivalent")

    # Runtime-source patches
    if "runtime-source" in p:
        return ("replace-openclaw-coupling", "mechanical",
                "Runtime source patch targeting OpenClaw internals")

    # Provider bootstrap/routing plugins
    if "provider-bootstrap" in p or "provider-routing" in p or "provider-lease" in p:
        return ("replace-openclaw-coupling", "mechanical",
                "Provider plugin with OpenClaw-specific coupling")

    # Handoff approvals
    if "handoff" in p.lower():
        return ("replace-openclaw-coupling", "mechanical",
                "Handoff mechanism tied to OpenClaw lease model")

    if "bin/ods-pixel-approve" in p or "bin/ods-pixel-route-lease" in p:
        return ("replace-openclaw-coupling", "mechanical",
                "Approval or lease CLI must be rebound to Hermes-native sessions")

    # Access mode config
    if "pixel_access_mode" in p or "access_mode_config" in p or "access_mode_server" in p:
        return ("replace-openclaw-coupling", "mechanical",
                "Access mode config referencing OpenClaw concepts")

    # Scope definitions
    if "scopes" in p.lower() and "test" not in p:
        return ("replace-openclaw-coupling", "mechanical",
                "Scope definition with OpenClaw coupling to replace")

    # Advice runtime
    if "advice_runtime" in p.lower() or "advice-runtime" in p.lower():
        return ("replace-openclaw-coupling", "mechanical",
                "Advice runtime with OpenClaw-specific logic")

    # Core pixel-agent files
    if subsystem == "core-runtime" and ("host/" in p or "plugin/" in p):
        return ("replace-openclaw-coupling", "mechanical",
                "Core pixel-agent host/plugin with OpenClaw coupling")

    # Edge service
    if subsystem == "ingress-edge":
        return ("retain-semantics", "mechanical",
                "Edge infrastructure with generic semantics to retain")

    # Dashboard API additions
    if subsystem == "dashboard-api" and "pixel" in p.lower() and "test" not in p:
        return ("retain-semantics", "mechanical",
                "Dashboard API with generic semantics to retain")

    # Config files
    if subsystem == "operations" and (".json" in p or ".yaml" in p or ".yml" in p):
        return ("orthogonal-retain", "mechanical",
                "Configuration file orthogonal to core runtime")

    # Installer scripts
    if subsystem == "installer":
        return ("orthogonal-retain", "mechanical",
                "Installer script orthogonal to core runtime")

    # Model routing config
    if subsystem == "model-routing" and ("config" in p or ".json" in p):
        return ("orthogonal-retain", "mechanical",
                "Model routing config orthogonal to core runtime")

    # Model routing code
    if subsystem == "model-routing":
        return ("retain-semantics", "mechanical",
                "Model routing code with generic semantics to retain")

    # Docs
    if "docs/" in p:
        return ("retain-semantics", "mechanical",
                "Documentation with generic semantics to retain")

    # Providers infrastructure
    if subsystem == "providers":
        return ("retain-semantics", "mechanical",
                "Provider infrastructure with generic semantics to retain")

    # Leases/approvals code (non-handoff)
    if subsystem == "leases-approvals":
        return ("retain-semantics", "mechanical",
                "Lease/approval code with generic semantics to retain")

    if subsystem == "previews":
        return ("retain-semantics", "mechanical",
                "Preview, artifact, or extension semantics to retain")

    if subsystem == "observability":
        return ("retain-semantics", "mechanical",
                "Observability or custody semantics to retain")

    # Test files
    if "test" in p.lower():
        return ("retain-semantics", "mechanical",
                "Test file with generic semantics to retain")

    # Catch-all: review-required
    return ("review-required", "mechanical",
            "Could not mechanically classify; requires integration review")


def get_git_diff(base: str, head: str, cwd: str):
    """Run git diff --name-status --no-renames BASE...HEAD and return list of (status, path)."""
    result = subprocess.run(
        ["git", "diff", "--name-status", "--no-renames", f"{base}...{head}"],
        capture_output=True, text=True, cwd=cwd, check=True,
    )
    records = []
    for line in result.stdout.strip().split("\n"):
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) == 2:
            records.append((parts[0].strip(), parts[1].strip()))
    return records


def compute_delta_sha256(raw_records: list) -> str:
    """Compute source_delta_sha256 from sorted change_type<TAB>path lines."""
    lines = []
    for change_type, path in raw_records:
        lines.append(f"{change_type}\t{path}")
    lines.sort()
    delta_text = "\n".join(lines) + "\n"
    return hashlib.sha256(delta_text.encode("utf-8")).hexdigest()


def generate_inventory(repo_dir: str):
    """Generate the full inventory from the two source PRs."""

    records = []
    pr_metadata = {}

    for pr_id, pr_def in PR_DEFS.items():
        base_sha = pr_def["base_sha"]
        head_sha = pr_def["head_sha"]

        raw_records = get_git_diff(base_sha, head_sha, repo_dir)
        delta_sha = compute_delta_sha256(raw_records)
        expected_count = pr_def["expected_changed_path_count"]
        expected_delta = pr_def["expected_source_delta_sha256"]
        if len(raw_records) != expected_count:
            raise RuntimeError(
                f"{pr_id}: observed {len(raw_records)} changed paths; "
                f"immutable expected count is {expected_count}"
            )
        if delta_sha != expected_delta:
            raise RuntimeError(
                f"{pr_id}: observed source delta {delta_sha}; "
                f"immutable expected digest is {expected_delta}"
            )

        pr_metadata[pr_id] = {
            "base_sha": base_sha,
            "head_sha": head_sha,
            "changed_path_count": len(raw_records),
            "source_delta_sha256": delta_sha,
        }

        for change_type, path in raw_records:
            subsystem = classify_subsystem(path)
            disposition, method, rationale = classify_disposition(path, subsystem, change_type)
            rec = {
                "source_pr": pr_id,
                "path": path,
                "change_type": change_type,
                "subsystem": subsystem,
                "disposition": disposition,
                "classification_method": method,
                "base_sha": base_sha,
                "head_sha": head_sha,
                "rationale": rationale,
            }
            records.append(rec)

    # Sort deterministically: (source_pr, path)
    records.sort(key=lambda r: (r["source_pr"], r["path"]))

    # Compute canonical digest (SHA-256 of JSON of sorted records)
    canonical = json.dumps(records, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # Subsystem totals
    subsystem_counts = Counter(r["subsystem"] for r in records)
    disposition_counts = Counter(r["disposition"] for r in records)

    inventory = {
        "version": 1,
        "ledger_id": "codex-portal-hermes-release-green-01a08297",
        "generated_at_branch": "feat/portal-hermes-agent",
        "branch_sha": "e6a3dd36aa2899bbc45919c04fe6027bfc72ca07",
        "source_prs": pr_metadata,
        "total_records": len(records),
        "canonical_digest": digest,
        "records": records,
        "subsystem_totals": dict(subsystem_counts.most_common()),
        "disposition_totals": dict(disposition_counts.most_common()),
        "policy": {
            "no_record_may_vanish": True,
            "no_record_may_duplicate": True,
            "no_record_may_move_to_drop_without_user_approval": True,
            "drop_requires": "explicit user-approval reference",
        },
    }

    return inventory


def main():
    parser = argparse.ArgumentParser(description="Generate Portal-on-Hermes predecessor inventory")
    parser.add_argument("--base-dir", required=True, help="Repository root directory")
    parser.add_argument("--out", default="inventory.json", help="Output file path")
    args = parser.parse_args()

    try:
        inventory = generate_inventory(args.base_dir)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        print(f"INVENTORY GENERATION FAILED: {error}", file=sys.stderr)
        return 1

    out_path = os.path.join(args.base_dir, args.out) if not os.path.isabs(args.out) else args.out
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(inventory, f, indent=2)
        f.write("\n")

    print(f"Generated inventory: {out_path}")
    print(f"  Total records: {inventory['total_records']}")
    print(f"  Canonical digest: {inventory['canonical_digest']}")
    for pr_id, meta in inventory["source_prs"].items():
        print(f"  {pr_id}: {meta['changed_path_count']} paths, delta={meta['source_delta_sha256']}")
    print(f"  Subsystem totals: {json.dumps(inventory['subsystem_totals'], indent=2)}")
    print(f"  Disposition totals: {json.dumps(inventory['disposition_totals'], indent=2)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
