#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${1:-${ROOT_DIR}/artifacts/installer-sim}"
mkdir -p "$OUT_DIR"

LINUX_LOG="${OUT_DIR}/linux-dryrun.log"
LINUX_SUMMARY_JSON="${OUT_DIR}/linux-install-summary.json"
MACOS_LOG="${OUT_DIR}/macos-installer.log"
WINDOWS_SIM_JSON="${OUT_DIR}/windows-preflight-sim.json"
MACOS_PREFLIGHT_JSON="${OUT_DIR}/macos-preflight.json"
MACOS_DOCTOR_JSON="${OUT_DIR}/macos-doctor.json"
DOCTOR_JSON="${OUT_DIR}/doctor.json"
SUMMARY_JSON="${OUT_DIR}/summary.json"
SUMMARY_MD="${OUT_DIR}/SUMMARY.md"
GOLDEN_CONTRACT_JSON="${ROOT_DIR}/config/golden-paths.json"
GOLDEN_EVIDENCE_JSON="${OUT_DIR}/golden-paths.json"

FAKEBIN="$(mktemp -d)"
trap 'rm -rf "$FAKEBIN"' EXIT
cat > "${FAKEBIN}/curl" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod +x "${FAKEBIN}/curl"

cd "$ROOT_DIR"

# 1) Linux installer dry-run simulation
LINUX_EXIT=0
if ! PATH="${FAKEBIN}:$PATH" bash install-core.sh --dry-run --non-interactive --skip-docker --force --summary-json "$LINUX_SUMMARY_JSON" >"$LINUX_LOG" 2>&1; then
  LINUX_EXIT=$?
fi

# 2) macOS installer MVP simulation
MACOS_EXIT=0
if ! bash installers/macos.sh --no-delegate --report "$MACOS_PREFLIGHT_JSON" --doctor-report "$MACOS_DOCTOR_JSON" >"$MACOS_LOG" 2>&1; then
  MACOS_EXIT=$?
fi

# 3) Windows scenario simulation via preflight engine (since pwsh may be unavailable in CI/sandbox)
scripts/preflight-engine.sh \
  --report "$WINDOWS_SIM_JSON" \
  --tier T1 \
  --ram-gb 16 \
  --disk-gb 120 \
  --gpu-backend nvidia \
  --gpu-vram-mb 12288 \
  --gpu-name "RTX 3060" \
  --platform-id windows \
  --compose-overlays docker-compose.base.yml,docker-compose.nvidia.yml \
  --script-dir "$ROOT_DIR" \
  --env >/dev/null

# 4) Doctor snapshot for current machine context
DOCTOR_EXIT=0
if ! scripts/ods-doctor.sh "$DOCTOR_JSON" >/dev/null 2>&1; then
  DOCTOR_EXIT=$?
fi

PYTHON_CMD="python3"
if [[ -f "$ROOT_DIR/lib/python-cmd.sh" ]]; then
  . "$ROOT_DIR/lib/python-cmd.sh"
  PYTHON_CMD="$(ods_detect_python_cmd)"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
fi

"$PYTHON_CMD" - "$SUMMARY_JSON" "$SUMMARY_MD" "$GOLDEN_EVIDENCE_JSON" "$GOLDEN_CONTRACT_JSON" "$LINUX_LOG" "$MACOS_LOG" "$WINDOWS_SIM_JSON" "$MACOS_PREFLIGHT_JSON" "$MACOS_DOCTOR_JSON" "$DOCTOR_JSON" "$LINUX_SUMMARY_JSON" "$LINUX_EXIT" "$MACOS_EXIT" "$DOCTOR_EXIT" <<'PY'
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

(
    summary_json_path,
    summary_md_path,
    golden_evidence_json_path,
    golden_contract_json_path,
    linux_log,
    macos_log,
    windows_sim_json,
    macos_preflight_json,
    macos_doctor_json,
    doctor_json,
    linux_install_summary_json,
    linux_exit,
    macos_exit,
    doctor_exit,
) = sys.argv[1:]

root_dir = pathlib.Path.cwd()

def load_json(path):
    p = pathlib.Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

linux_text = pathlib.Path(linux_log).read_text(encoding="utf-8", errors="replace") if pathlib.Path(linux_log).exists() else ""
macos_text = pathlib.Path(macos_log).read_text(encoding="utf-8", errors="replace") if pathlib.Path(macos_log).exists() else ""

linux_signals = {
    "capability_loaded": bool(re.search(r"Capability profile loaded", linux_text)),
    "hardware_class_logged": bool(re.search(r"Hardware class:", linux_text)),
    "backend_contract_loaded": bool(re.search(r"Backend contract loaded", linux_text)),
    "preflight_report_logged": bool(re.search(r"Preflight report:", linux_text)),
    "compose_selection_logged": bool(re.search(r"Compose selection:", linux_text)),
}

summary = {
    "version": "1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "runs": {
        "linux_dryrun": {
            "exit_code": int(linux_exit),
            "signals": linux_signals,
            "log": linux_log,
            "install_summary": load_json(linux_install_summary_json) or {},
        },
        "macos_installer_mvp": {
            "exit_code": int(macos_exit),
            "log": macos_log,
            "preflight": load_json(macos_preflight_json),
            "doctor": load_json(macos_doctor_json),
        },
        "windows_scenario_preflight": {
            "report": load_json(windows_sim_json),
        },
        "doctor_snapshot": {
            "exit_code": int(doctor_exit),
            "report": load_json(doctor_json),
        },
    },
}

def run_status(run_name, run):
    if run_name == "linux_dryrun":
        signals = run.get("signals") or {}
        return {
            "ok": run.get("exit_code") == 0 and all(signals.values()),
            "exit_code": run.get("exit_code"),
            "required_signals": signals,
        }
    if run_name == "macos_installer_mvp":
        summary_block = ((run.get("preflight") or {}).get("summary") or {})
        blockers = summary_block.get("blockers")
        return {
            "ok": run.get("exit_code") == 0 and (blockers in (0, None)),
            "exit_code": run.get("exit_code"),
            "blockers": blockers,
            "warnings": summary_block.get("warnings"),
        }
    if run_name == "windows_scenario_preflight":
        summary_block = ((run.get("report") or {}).get("summary") or {})
        blockers = summary_block.get("blockers")
        return {
            "ok": blockers == 0,
            "blockers": blockers,
            "warnings": summary_block.get("warnings"),
        }
    return {"ok": run is not None}

def build_golden_evidence():
    contract = load_json(golden_contract_json_path) or {"scenarios": []}
    evidence = {
        "version": "1",
        "generated_at": summary["generated_at"],
        "contract": golden_contract_json_path,
        "scenarios": [],
    }

    runs = summary.get("runs") or {}
    for scenario in contract.get("scenarios", []):
        installer = scenario.get("installer") or {}
        expected = scenario.get("expected") or {}
        run_name = installer.get("ci_simulation")
        run = runs.get(run_name) if run_name else None
        compose_files = expected.get("compose_files") or []
        generated_configs = expected.get("generated_configs") or []
        health_checks = expected.get("health_checks") or []
        missing_compose = [
            path for path in compose_files
            if not (root_dir / path).exists()
        ]

        status = run_status(run_name, run or {})
        scenario_ok = bool(status.get("ok")) and not missing_compose
        evidence["scenarios"].append({
            "id": scenario.get("id"),
            "label": scenario.get("label"),
            "status": "pass" if scenario_ok else "fail",
            "simulation_run": run_name,
            "simulation_status": status,
            "expected": {
                "ods_mode": expected.get("ods_mode"),
                "llm_backend": expected.get("llm_backend"),
                "llm_host_port": expected.get("llm_host_port"),
                "llm_container_port": expected.get("llm_container_port"),
                "model_route": expected.get("model_route") or {},
                "compose_files": compose_files,
                "generated_config_surfaces": [
                    item.get("surface") for item in generated_configs
                    if isinstance(item, dict)
                ],
                "health_check_services": [
                    item.get("service") for item in health_checks
                    if isinstance(item, dict)
                ],
            },
            "checks": {
                "compose_files_exist": not missing_compose,
                "missing_compose_files": missing_compose,
                "generated_config_count": len(generated_configs),
                "health_check_count": len(health_checks),
            },
        })
    return evidence

golden_evidence = build_golden_evidence()
summary["golden_paths"] = {
    "contract": golden_contract_json_path,
    "evidence": golden_evidence_json_path,
    "scenario_count": len(golden_evidence.get("scenarios", [])),
    "pass_count": sum(1 for item in golden_evidence.get("scenarios", []) if item.get("status") == "pass"),
}

pathlib.Path(summary_json_path).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
pathlib.Path(golden_evidence_json_path).write_text(json.dumps(golden_evidence, indent=2) + "\n", encoding="utf-8")

lines = []
lines.append("# Installer Simulation Summary")
lines.append("")
lines.append(f"Generated: {summary['generated_at']}")
lines.append("")
lines.append("## Linux Dry-Run")
lines.append(f"- Exit code: {linux_exit}")
for k, v in linux_signals.items():
    lines.append(f"- {k}: {'yes' if v else 'no'}")
lines.append(f"- Log: `{linux_log}`")
lines.append("")

mp = summary["runs"]["macos_installer_mvp"].get("preflight") or {}
ms = (mp.get("summary") or {})
lines.append("## macOS Installer MVP")
lines.append(f"- Exit code: {macos_exit}")
lines.append(f"- Preflight blockers: {ms.get('blockers', 'n/a')}")
lines.append(f"- Preflight warnings: {ms.get('warnings', 'n/a')}")
lines.append(f"- Log: `{macos_log}`")
lines.append(f"- Preflight JSON: `{macos_preflight_json}`")
lines.append(f"- Doctor JSON: `{macos_doctor_json}`")
lines.append("")

wp = summary["runs"]["windows_scenario_preflight"].get("report") or {}
ws = (wp.get("summary") or {})
lines.append("## Windows Scenario (Simulated)")
lines.append(f"- Preflight blockers: {ws.get('blockers', 'n/a')}")
lines.append(f"- Preflight warnings: {ws.get('warnings', 'n/a')}")
lines.append(f"- Report: `{windows_sim_json}`")
lines.append("")

dr = summary["runs"]["doctor_snapshot"].get("report") or {}
dsum = dr.get("summary") or {}
lines.append("## Doctor Snapshot")
lines.append(f"- Exit code: {doctor_exit}")
lines.append(f"- Runtime ready: {dsum.get('runtime_ready', 'n/a')}")
lines.append(f"- Report: `{doctor_json}`")
lines.append("")
lines.append("## Golden Paths")
for item in golden_evidence.get("scenarios", []):
    status = item.get("status", "unknown")
    label = item.get("label") or item.get("id")
    run_name = item.get("simulation_run") or "n/a"
    lines.append(f"- {label}: {status} via `{run_name}`")
lines.append(f"- Evidence JSON: `{golden_evidence_json_path}`")

pathlib.Path(summary_md_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

if [[ -x "${ROOT_DIR}/scripts/validate-sim-summary.py" ]]; then
  "${ROOT_DIR}/scripts/validate-sim-summary.py" "$SUMMARY_JSON"
fi

echo "Installer simulation complete."
echo "  JSON: $SUMMARY_JSON"
echo "  MD:   $SUMMARY_MD"
echo "  Golden paths: $GOLDEN_EVIDENCE_JSON"
