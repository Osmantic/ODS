"""Tests for ods/scripts/vllm-gpu-admission.py using injected boundaries."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "vllm-gpu-admission.py"


def _load():
    spec = importlib.util.spec_from_file_location("vllm_gpu_admission", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["vllm_gpu_admission"] = mod
    spec.loader.exec_module(mod)
    return mod


mod = _load()
AdmissionError = mod.AdmissionError

INSTALL = "/opt/ods"
CID = "a" * 64
CID2 = "b" * 64
GPU = "GPU-1111"
GPU2 = "GPU-2222"


def _engine(cid=CID, service="vllm-server", started="2024-01-01T00:00:00Z", pid=100):
    return {"id": cid, "service": service, "started_at": started, "pid": pid}


def _cgroup(cid):
    return f"0::/system.slice/docker-{cid}.scope\n"


def _rows(free=1000, total=8000, uuid=GPU):
    return [f"{uuid}, 8.9, {total}, {free}"]


def _run(rows, engine, apps_text="", cgroup_map=None, verify_ok=True, inspect_raises=False):
    cgroup_map = cgroup_map or {}

    def inspect_fn(_d):
        if inspect_raises:
            raise AdmissionError("docker down")
        return engine

    def verify_fn(_d, _e):
        return verify_ok

    def apps_fn():
        return apps_text

    def read_fn(pid):
        if pid not in cgroup_map:
            raise AdmissionError(f"no cgroup {pid}")
        return cgroup_map[pid]

    return mod.adjust_rows(
        rows, INSTALL,
        inspect_fn=inspect_fn, verify_fn=verify_fn,
        apps_fn=apps_fn, read_cgroup_fn=read_fn,
    )


def _free(out_line):
    return int(out_line.split(",")[3].strip())


# ── credit correctness ──────────────────────────────────────────────────────

def test_llama_credit():
    eng = _engine(service="llama-server")
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 1500


def test_vllm_credit():
    eng = _engine(service="vllm-server")
    apps = f"{GPU}, 100, 700\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 1700


def test_wrong_path_no_credit():
    eng = _engine()
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID2)})
    assert _free(out[0]) == 1000


def test_wrong_service_no_credit():
    eng = _engine(service="other")
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 1000


def test_wrong_id_no_credit():
    eng = _engine()
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID2)})
    assert _free(out[0]) == 1000


def test_ambiguous_cgroup_no_credit():
    eng = _engine()
    apps = f"{GPU}, 100, 500\n"
    cg = _cgroup(CID) + _cgroup(CID2)
    out = _run(_rows(free=1000), eng, apps, {100: cg})
    assert _free(out[0]) == 1000


def test_restarted_pid_no_credit():
    eng = _engine()
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID)}, verify_ok=False)
    assert _free(out[0]) == 1000


def test_unreadable_process_no_credit():
    eng = _engine()
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {})
    assert _free(out[0]) == 1000


def test_duplicate_identical_once():
    eng = _engine()
    apps = f"{GPU}, 100, 500\n{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 1500


def test_duplicate_conflicting_no_credit():
    eng = _engine()
    apps = f"{GPU}, 100, 500\n{GPU}, 100, 700\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 1000


def test_na_process_skipped():
    eng = _engine()
    apps = f"{GPU}, 100, [N/A]\n{GPU}, 101, 300\n"
    out = _run(_rows(free=1000), eng, apps, {101: _cgroup(CID)})
    assert _free(out[0]) == 1300


def test_na_free_rejected():
    with pytest.raises(AdmissionError):
        _run([f"{GPU}, 8.9, 8000, [N/A]"], _engine())


def test_no_own_engine_no_credit():
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), None, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 1000


def test_unrelated_deepseek_no_credit():
    eng = _engine(service="deepseek")
    apps = f"{GPU}, 100, 500\n"
    out = _run(_rows(free=1000), eng, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 1000


def test_idle_free_when_docker_unavailable():
    out = _run(_rows(free=1000), None, "", {}, inspect_raises=True)
    assert _free(out[0]) == 1000


def test_totals_clamp():
    eng = _engine()
    apps = f"{GPU}, 100, 5000\n"
    out = _run(_rows(free=7000, total=8000), eng, apps, {100: _cgroup(CID)})
    assert _free(out[0]) == 8000


def test_free_exceeds_total_rejected():
    with pytest.raises(AdmissionError):
        _run([f"{GPU}, 8.9, 8000, 9000"], _engine())


def test_total_nonpositive_rejected():
    with pytest.raises(AdmissionError):
        _run([f"{GPU}, 8.9, 0, 0"], _engine())


# ── inspect_engine behavior ─────────────────────────────────────────────────

def _inspect_entry(cid=CID, service="vllm-server", wd=INSTALL, running=True, pid=100, started="2024-01-01T00:00:00Z"):
    return {
        "Id": cid,
        "State": {"Running": running, "Pid": pid, "StartedAt": started},
        "Config": {"Labels": {
            "com.docker.compose.service": service,
            "com.docker.compose.project.working_dir": wd,
        }},
    }


def _inspect_runner(entries_by_id, ps_ids):
    def run(args):
        if args[0] == "ps":
            return "\n".join(ps_ids) + "\n"
        if args[0] == "inspect":
            cid = args[-1]
            if cid not in entries_by_id:
                raise AdmissionError("no such container")
            return json.dumps([entries_by_id[cid]])
        raise AdmissionError("unexpected")
    return run


def test_inspect_returns_engine():
    runner = _inspect_runner({CID: _inspect_entry()}, [CID])
    eng = mod.inspect_engine(INSTALL, run_docker_fn=runner)
    assert eng == {"id": CID, "service": "vllm-server",
                   "started_at": "2024-01-01T00:00:00Z", "pid": 100}


def test_inspect_absent_name_does_not_kill_other():
    runner = _inspect_runner({CID: _inspect_entry()}, ["missing", CID])
    eng = mod.inspect_engine(INSTALL, run_docker_fn=runner)
    assert eng is not None and eng["id"] == CID


def test_inspect_two_valid_engines_ambiguous():
    runner = _inspect_runner(
        {CID: _inspect_entry(), CID2: _inspect_entry(cid=CID2, service="llama-server")},
        [CID, CID2],
    )
    with pytest.raises(AdmissionError):
        mod.inspect_engine(INSTALL, run_docker_fn=runner)


def test_inspect_wrong_working_dir_ignored():
    runner = _inspect_runner({CID: _inspect_entry(wd="/other")}, [CID])
    assert mod.inspect_engine(INSTALL, run_docker_fn=runner) is None


def test_inspect_not_running_ignored():
    runner = _inspect_runner({CID: _inspect_entry(running=False)}, [CID])
    assert mod.inspect_engine(INSTALL, run_docker_fn=runner) is None


def test_inspect_zero_pid_ignored():
    runner = _inspect_runner({CID: _inspect_entry(pid=0)}, [CID])
    assert mod.inspect_engine(INSTALL, run_docker_fn=runner) is None


def test_inspect_uses_type_container():
    seen = []
    def run(args):
        seen.append(list(args))
        if args[0] == "ps":
            return CID + "\n"
        return json.dumps([_inspect_entry()])
    mod.inspect_engine(INSTALL, run_docker_fn=run)
    inspect_calls = [a for a in seen if a[0] == "inspect"]
    assert inspect_calls and all("--type" in a and "container" in a for a in inspect_calls)


# ── cgroup parsing ──────────────────────────────────────────────────────────

def test_cgroup_path_extracted_before_match():
    # ID in controllers field must not count; only path matters.
    text = f"0::/docker/{CID}\n"
    assert mod.container_id_from_cgroup(text) == CID


def test_cgroup_substring_suffix_rejected():
    text = f"0::/docker/{CID}extra\n"
    assert mod.container_id_from_cgroup(text) is None


def test_cgroup_multiple_lines_same_id():
    text = f"0::/docker/{CID}\n1:name=systemd:/docker/{CID}\n"
    assert mod.container_id_from_cgroup(text) == CID


def test_cgroup_multiple_lines_different_ids_ambiguous():
    text = f"0::/docker/{CID}\n1:name=systemd:/docker/{CID2}\n"
    assert mod.container_id_from_cgroup(text) is None


def _contract_rows(free=1000, total=8000, uuid=GPU):
    return [f"{uuid}, 10.0, {total}, {free}"]


def _contract_catalog(tmp_path, **overrides):
    from test_hf_manifest_download import write_catalog, FILES
    return write_catalog(tmp_path / "model-library.json", FILES, **overrides)


def _contract(rows, catalog, *, model_id="test-model-nvfp4-vllm", gpu_uuid=GPU,
              memory_share=None, engine=None, apps_text="", cgroup_map=None,
              verify_ok=True, inspect_raises=False):
    cgroup_map = cgroup_map or {}

    def inspect_fn(_d):
        if inspect_raises:
            raise AdmissionError("docker down")
        return engine

    def verify_fn(_d, _e):
        return verify_ok

    def apps_fn():
        return apps_text

    def read_fn(pid):
        if pid not in cgroup_map:
            raise AdmissionError(f"no cgroup {pid}")
        return cgroup_map[pid]

    return mod.verify_contract(
        rows, INSTALL,
        model_id=model_id, gpu_uuid=gpu_uuid, memory_share=memory_share,
        catalog_path=str(catalog),
        inspect_fn=inspect_fn, verify_fn=verify_fn,
        apps_fn=apps_fn, read_cgroup_fn=read_fn,
    )


def test_contract_accepts_when_share_fits(tmp_path):
    catalog = _contract_catalog(tmp_path)
    # The configured half-card share requires 5024 MiB including the context.
    _contract(_contract_rows(free=5100), catalog, memory_share="0.5")


def test_contract_rejects_when_share_too_large(tmp_path):
    catalog = _contract_catalog(tmp_path)
    with pytest.raises(AdmissionError):
        _contract(_contract_rows(free=5000), catalog, memory_share="0.85")


def test_contract_empty_share_uses_runtime_default(tmp_path):
    catalog = _contract_catalog(tmp_path, gpu_memory_utilization=0.5)
    # Default 0.9 * 8000 + 1024 = 8224 > 8000, so 8000 free still fails.
    with pytest.raises(AdmissionError):
        _contract(_contract_rows(free=8000), catalog, memory_share=None)


def test_contract_idle_gpu_does_not_permit_busy_selected(tmp_path):
    catalog = _contract_catalog(tmp_path)
    rows = [f"{GPU}, 10.0, 8000, 100", f"{GPU2}, 10.0, 8000, 8000"]
    with pytest.raises(AdmissionError):
        _contract(rows, catalog, gpu_uuid=GPU, memory_share="0.5")


def test_contract_missing_selected_gpu(tmp_path):
    catalog = _contract_catalog(tmp_path)
    with pytest.raises(AdmissionError):
        _contract(_contract_rows(free=8000, uuid=GPU2), catalog, gpu_uuid=GPU, memory_share="0.5")


def test_contract_duplicate_selected_uuid(tmp_path):
    catalog = _contract_catalog(tmp_path)
    rows = [f"{GPU}, 10.0, 8000, 8000", f"{GPU}, 10.0, 8000, 8000"]
    with pytest.raises(AdmissionError):
        _contract(rows, catalog, memory_share="0.5")


def test_contract_invalid_free_na(tmp_path):
    catalog = _contract_catalog(tmp_path)
    with pytest.raises(AdmissionError):
        _contract([f"{GPU}, 10.0, 8000, [N/A]"], catalog, memory_share="0.5")


@pytest.mark.parametrize("share", ["nan", "inf", "-0.1", "1.5"])
def test_contract_invalid_share(tmp_path, share):
    catalog = _contract_catalog(tmp_path)
    with pytest.raises(AdmissionError):
        _contract(_contract_rows(free=8000), catalog, memory_share=share)


def test_contract_wrong_compute(tmp_path):
    catalog = _contract_catalog(tmp_path)
    with pytest.raises(AdmissionError):
        _contract([f"{GPU}, 9.0, 8000, 8000"], catalog, memory_share="0.5")


def test_contract_no_exact_catalog_id(tmp_path):
    catalog = _contract_catalog(tmp_path)
    with pytest.raises(AdmissionError):
        _contract(_contract_rows(free=8000), catalog, model_id="missing-model", memory_share="0.5")


def test_contract_own_engine_credit_permits(tmp_path):
    catalog = _contract_catalog(tmp_path)
    eng = _engine()
    apps = f"{GPU}, 100, 3000\n"
    # 0.5 * 8000 + 1024 = 5024; free 2500 + 3000 credit = 5500 >= 5024.
    _contract(_contract_rows(free=2500), catalog, memory_share="0.5", engine=eng,
              apps_text=apps, cgroup_map={100: _cgroup(CID)})


def test_contract_restarted_engine_no_credit(tmp_path):
    catalog = _contract_catalog(tmp_path)
    eng = _engine()
    apps = f"{GPU}, 100, 3000\n"
    with pytest.raises(AdmissionError):
        _contract(_contract_rows(free=2500), catalog, memory_share="0.5", engine=eng,
                  apps_text=apps, cgroup_map={100: _cgroup(CID)}, verify_ok=False)


def test_contract_wrong_container_no_credit(tmp_path):
    catalog = _contract_catalog(tmp_path)
    eng = _engine()
    apps = f"{GPU}, 100, 3000\n"
    with pytest.raises(AdmissionError):
        _contract(_contract_rows(free=2500), catalog, memory_share="0.5", engine=eng,
                  apps_text=apps, cgroup_map={100: _cgroup(CID2)})


def test_cli_missing_options_exit1(tmp_path, monkeypatch, capsys):
    catalog = _contract_catalog(tmp_path)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: f"{GPU}, 10.0, 8000, 8000\n"})())
    rc = mod.main(["--install-dir", INSTALL, "--model-id", "test-model-nvfp4-vllm",
                   "--catalog", str(catalog)])
    assert rc == 1


def test_cli_contract_error_exit1(tmp_path, monkeypatch, capsys):
    catalog = _contract_catalog(tmp_path)
    monkeypatch.setattr(sys, "stdin", type("S", (), {"read": lambda self: f"{GPU}, 10.0, 8000, 100\n"})())
    monkeypatch.setattr(mod, "inspect_engine", lambda d: None)
    rc = mod.main(["--install-dir", INSTALL, "--model-id", "test-model-nvfp4-vllm",
                   "--gpu-uuid", GPU, "--memory-share", "0.5", "--catalog", str(catalog)])
    assert rc == 1
