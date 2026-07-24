#!/usr/bin/env python3
"""Universal health-probe governance eval (stateless, uv-friendly).

20 learning-loop iterations scoring sprawl, shadows, gates, and capability coverage.
No network. Stdlib only. Run: uv run --python 3.12 --no-project scripts/health-probe-universal-eval.py
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts" / "health-probe-eval"
OUT.mkdir(parents=True, exist_ok=True)

# Capability map: 0 missing, 1 partial, 2 complete+gated
BASE = {
    "registry_helpers": 2,
    "sr_http_probe_2xx": 2,
    "consumers_status_cli": 2,
    "consumers_doctor_voice": 2,
    "consumers_doctor_dashboard_webui": 1,
    "consumers_showcase": 2,
    "consumers_first_boot": 1,
    "consumers_validate_core": 1,
    "shadow_audit_gate": 2,
    "schema_drift_gate": 2,
    "contract_test": 2,
    "merge_order_adr": 2,
    "ai_rulez_governance": 0,
    "path_shell_audit": 0,
    "ci_green_fork": 0,
    "review_approved": 0,
    "health_type_tcp_none": 0,
    "external_llm_doctor": 0,
    "single_schema_source": 1,
    "bash_python_parity": 1,
}

WEIGHTS = {k: 4 for k in BASE}
WEIGHTS.update(
    {
        "registry_helpers": 6,
        "shadow_audit_gate": 5,
        "schema_drift_gate": 5,
        "contract_test": 5,
        "ci_green_fork": 5,
        "review_approved": 4,
        "merge_order_adr": 4,
        "ai_rulez_governance": 4,
        "path_shell_audit": 3,
        "health_type_tcp_none": 4,
        "external_llm_doctor": 3,
    }
)

IMPROVEMENTS = [
    ("migrate_doctor_dashboard_webui", {"consumers_doctor_dashboard_webui": 2}),
    ("migrate_first_boot_demo", {"consumers_first_boot": 2}),
    ("migrate_validate_core", {"consumers_validate_core": 2}),
    ("add_ai_rulez", {"ai_rulez_governance": 2}),
    ("add_path_shell_audit", {"path_shell_audit": 2}),
    ("land_pr_1934_ci_review", {"ci_green_fork": 2, "review_approved": 2}),
    ("add_health_type", {"health_type_tcp_none": 2}),
    ("external_llm", {"external_llm_doctor": 2}),
    ("unify_schema", {"single_schema_source": 2}),
    ("bash_python_dto", {"bash_python_parity": 2}),
]


def score(caps: dict[str, int]) -> float:
    total = sum(WEIGHTS[k] * caps[k] for k in WEIGHTS)
    max_total = sum(WEIGHTS[k] * 2 for k in WEIGHTS)
    return round(100.0 * total / max_total, 2)


def file_has(path: Path, pattern: str) -> bool:
    if not path.exists():
        return False
    return re.search(pattern, path.read_text(encoding="utf-8", errors="replace")) is not None


def probe_live_caps(base: dict[str, int]) -> dict[str, int]:
    caps = base.copy()
    # Evidence from workspace
    if (ROOT.parent / ".ai-rulez" / "config.toml").exists():
        caps["ai_rulez_governance"] = 2
    if (ROOT / "tests" / "test-health-probe-path-shell-audit.sh").exists():
        caps["path_shell_audit"] = 2
    doctor = ROOT / "scripts" / "ods-doctor.sh"
    if file_has(doctor, r"sr_curl_health dashboard") and file_has(doctor, r"sr_curl_health open-webui"):
        caps["consumers_doctor_dashboard_webui"] = 2
    demo = ROOT / "scripts" / "first-boot-demo.sh"
    if file_has(demo, r"check_service_id"):
        caps["consumers_first_boot"] = 2
    validate = ROOT / "scripts" / "validate.sh"
    if file_has(validate, r'check_registry_health "llama-server health"'):
        caps["consumers_validate_core"] = 2
    if (ROOT / "docs" / "ADR-HEALTH-PROBE-MERGE-ORDER.md").exists():
        caps["merge_order_adr"] = 2
    return caps


def run_gate(cmd: list[str]) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode == 0, out[-2000:]
    except Exception as exc:  # narrow intentional: eval harness boundary
        return False, str(exc)


@dataclass
class Iteration:
    n: int
    action: str
    score_before: float
    score_after: float
    delta: float
    notes: str


def main() -> None:
    caps = probe_live_caps(BASE)
    history: list[Iteration] = []
    s0 = score(caps)
    history.append(Iteration(0, "live_evidence_baseline", s0, s0, 0.0, "probed workspace files"))

    preferred = [name for name, _ in IMPROVEMENTS]
    queue = preferred.copy()
    applied = []

    for n in range(1, 21):
        before = score(caps)
        if not queue:
            action = "residual_reprobe"
            after_caps = probe_live_caps(caps)
            notes = "re-probed filesystem"
        else:
            action = queue.pop(0)
            boost = dict(IMPROVEMENTS)[action]
            # Only credit boosts already evidenced in tree (no fantasy scoring)
            after_caps = caps.copy()
            live = probe_live_caps(BASE)
            for k, v in boost.items():
                if live.get(k, 0) >= v:
                    after_caps[k] = max(after_caps.get(k, 0), v)
                    notes = f"evidenced {action}"
                else:
                    notes = f"planned {action} not yet evidenced — no score inflation"
            applied.append(action)
        after = score(after_caps)
        history.append(
            Iteration(n, action, before, after, round(after - before, 2), notes)
        )
        caps = after_caps

    # Run real gates
    gates = {}
    for name, script in [
        ("shadow_audit", "tests/test-health-probe-shadow-audit.sh"),
        ("schema_drift", "tests/test-service-manifest-health-contract-drift.sh"),
        ("path_shell", "tests/test-health-probe-path-shell-audit.sh"),
    ]:
        ok, out = run_gate(["bash", script])
        gates[name] = {"ok": ok, "tail": out[-500:]}

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_score": s0,
        "final_score": score(caps),
        "capabilities": caps,
        "gaps": sorted([k for k, v in caps.items() if v < 2]),
        "history": [asdict(h) for h in history],
        "gates": gates,
        "wrong": [
            "Ghost empty feature worktrees",
            "curl -sf for registry health (redirects healthy)",
            "Dual schema health-field drift without gate",
            "Overlapping PRs without foundation-first order",
            "Fork CI action_required without maintainer approval",
            "Fantasy eval scores without filesystem evidence",
        ],
        "right": [
            "Registry-owned health_port/env/header + 2xx helpers",
            "Contract + shadow + schema-drift gates",
            "ADR merge order 1934→1692→1743",
            ".ai-rulez health-probe domain",
            "Stateless uv/ollama/codex/grok evaluation",
            "Path/shell audit of health surfaces",
        ],
        "top10": preferred,
        "skills_to_utilize": [
            "systematic-debugging",
            "verification-before-completion",
            "native-first-stateless",
            "mcp-fleet-routing",
            "check-work",
            "pr-babysit",
            "code-review",
        ],
        "cli_tools_to_implement": [
            "gh pr checks / merge (maintainer)",
            "uv run eval harness",
            "bash tests/*health*",
            "codex exec -s read-only --ephemeral",
            "grok -p single-turn",
            "ollama-mcp generate/chat",
            "shellcheck -x -S warning (when available)",
        ],
    }
    (OUT / "universal-eval-20.json").write_text(json.dumps(report, indent=2))
    md = [
        f"# Universal Health Probe Eval ({datetime.now(timezone.utc).date()})",
        f"Baseline **{report['baseline_score']}** → Final **{report['final_score']}**",
        "",
        "## Gaps remaining",
        ", ".join(report["gaps"]) or "(none)",
        "",
        "## Gates",
    ]
    for k, v in gates.items():
        md.append(f"- {k}: {'PASS' if v['ok'] else 'FAIL'}")
    md += ["", "## Top 10", ""]
    for i, t in enumerate(preferred, 1):
        md.append(f"{i}. {t}")
    (OUT / "universal-eval-20.md").write_text("\n".join(md) + "\n")
    print(json.dumps({
        "baseline": report["baseline_score"],
        "final": report["final_score"],
        "gaps": report["gaps"],
        "gates": {k: v["ok"] for k, v in gates.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
