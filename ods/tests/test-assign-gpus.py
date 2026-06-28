import json
import os
import subprocess
import sys

SCRIPT = os.path.join(os.path.dirname(__file__), "../scripts/assign_gpus.py")
FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures/topology_json")

def fixture_path(name):
    return os.path.join(FIXTURES_DIR, name)

def run(topology_path, model_size_mb, enabled_services=None):
    cmd = [sys.executable, SCRIPT, "--topology", topology_path, "--model-size", str(model_size_mb)]
    if enabled_services is not None:
        cmd += ["--enabled-services", enabled_services]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = None
    if result.returncode == 0:
        output = json.loads(result.stdout)["gpu_assignment"]
    return result.returncode, output, result.stderr

def all_assigned_uuids(output):
    uuids = set()
    for svc in output["services"].values():
        uuids.update(svc["gpus"])
    return uuids

def llama(output):
    return output["services"]["llama_server"]

def parallelism(output):
    return llama(output)["parallelism"]


# ── 1 GPU — single ────────────────────────────────────────────────────────────

class TestSingleGpu:
    TOPO = fixture_path("nvidia_smi_topo_matrix_1gpu_pcie.json")
    UUID = "GPU-12345678-1234-1234-1234-123456789012"

    def test_strategy_is_single(self):
        _, out, _ = run(self.TOPO, 20000)
        assert out["strategy"] == "single"

    def test_all_services_share_only_gpu(self):
        _, out, _ = run(self.TOPO, 20000)
        for svc in out["services"].values():
            assert svc["gpus"] == [self.UUID]

    def test_llama_mode_none(self):
        _, out, _ = run(self.TOPO, 20000)
        p = parallelism(out)
        assert p["mode"] == "none"
        assert p["tensor_parallel_size"] == 1
        assert p["pipeline_parallel_size"] == 1

    def test_model_too_large_errors(self):
        rc, _, stderr = run(self.TOPO, 30000)
        assert rc == 1
        assert "exceeds" in stderr.lower()

    def test_model_exactly_fits(self):
        rc, out, _ = run(self.TOPO, 24576)
        assert rc == 0
        assert out["strategy"] == "single"

    def test_no_topology_analysis_needed(self):
        rc, out, _ = run(self.TOPO, 10000)
        assert rc == 0


# ── 1 GPU — custom --enabled-services ─────────────────────────────────────────

class TestSingleGpuEnabledServices:
    """Regression: the single-GPU path must not crash when --enabled-services
    omits llama_server, and must still assign llama_server (as the multi-GPU
    path does)."""
    TOPO = fixture_path("nvidia_smi_topo_matrix_1gpu_pcie.json")
    UUID = "GPU-12345678-1234-1234-1234-123456789012"

    def test_omitting_llama_server_does_not_crash(self):
        rc, _, stderr = run(self.TOPO, 10000, enabled_services="whisper,embeddings")
        assert rc == 0, stderr

    def test_llama_server_assigned_even_when_omitted(self):
        _, out, _ = run(self.TOPO, 10000, enabled_services="whisper,embeddings")
        assert out["services"]["llama_server"]["gpus"] == [self.UUID]
        assert parallelism(out)["mode"] == "none"

    def test_only_requested_services_plus_llama(self):
        _, out, _ = run(self.TOPO, 10000, enabled_services="whisper,embeddings")
        assert set(out["services"]) == {"llama_server", "whisper", "embeddings"}

    def test_explicit_llama_server_unchanged(self):
        _, out, _ = run(self.TOPO, 10000, enabled_services="llama_server,whisper")
        assert set(out["services"]) == {"llama_server", "whisper"}
        assert out["services"]["whisper"]["gpus"] == [self.UUID]


# ── 2 GPU — rank-first means PHB pair always wins over single GPU ─────────────

class TestTwoGpuColoc:
    TOPO = fixture_path("nvidia_smi_topo_matrix_2gpus_phb_coloc.json")
    GPU0 = "GPU-00000000-0000-0000-0000-000000000000"
    GPU1 = "GPU-11111111-1111-1111-1111-111111111111"

    def test_model_fits_one_gpu_rank_first_takes_pair(self):
        # rank-first: PHB pair rank=30 beats single rank=0,
        # so llama always gets both GPUs when there are only 2
        _, out, _ = run(self.TOPO, 20000)
        assert set(llama(out)["gpus"]) == {self.GPU0, self.GPU1}

    def test_model_fits_one_gpu_strategy_colocated(self):
        # remaining=0 after llama takes both → colocated
        _, out, _ = run(self.TOPO, 20000)
        assert out["strategy"] == "colocated"

    def test_model_fits_one_gpu_services_share_last(self):
        _, out, _ = run(self.TOPO, 20000)
        for name in ("whisper", "comfyui", "embeddings"):
            assert out["services"][name]["gpus"] == [self.GPU1]

    def test_model_fits_one_gpu_pipeline(self):
        # PHB rank=30, n=2 → pipeline
        _, out, _ = run(self.TOPO, 20000)
        p = parallelism(out)
        assert p["mode"] == "pipeline"
        assert p["tensor_parallel_size"] == 1
        assert p["pipeline_parallel_size"] == 2

    def test_model_needs_both_gpus_strategy_colocated(self):
        _, out, _ = run(self.TOPO, 30000)
        assert out["strategy"] == "colocated"

    def test_model_needs_both_gpus_llama_gets_both(self):
        _, out, _ = run(self.TOPO, 30000)
        assert set(llama(out)["gpus"]) == {self.GPU0, self.GPU1}

    def test_model_needs_both_gpus_services_share_llamas_last(self):
        _, out, _ = run(self.TOPO, 30000)
        for name in ("whisper", "comfyui", "embeddings"):
            assert out["services"][name]["gpus"] == [self.GPU1]

    def test_model_needs_both_gpus_llama_pipeline(self):
        _, out, _ = run(self.TOPO, 30000)
        p = parallelism(out)
        assert p["mode"] == "pipeline"
        assert p["tensor_parallel_size"] == 1
        assert p["pipeline_parallel_size"] == 2

    def test_no_gpu_idle(self):
        for model_size in (20000, 30000):
            _, out, _ = run(self.TOPO, model_size)
            assert all_assigned_uuids(out) == {self.GPU0, self.GPU1}


# ── 4 GPU — SOC / cross-NUMA PCIe ────────────────────────────────────────────

class TestFourGpuSoc:
    """4x A100-80GB. GPUs 0-1 and 2-3 are PHB pairs rank=30, cross pairs SOC rank=10."""
    TOPO = fixture_path("nvidia_smi_topo_matrix_4gpus_soc.json")
    UUIDS = [
        "GPU-00000000-0000-0000-0000-000000000000",
        "GPU-11111111-1111-1111-1111-111111111111",
        "GPU-22222222-2222-2222-2222-222222222222",
        "GPU-33333333-3333-3333-3333-333333333333",
    ]

    def test_model_fits_one_gpu_picks_phb_pair(self):
        # rank-first: PHB pair rank=30 beats single rank=0
        _, out, _ = run(self.TOPO, 70000)
        llama_uuids = set(llama(out)["gpus"])
        phb_pair_a = {self.UUIDS[0], self.UUIDS[1]}
        phb_pair_b = {self.UUIDS[2], self.UUIDS[3]}
        assert llama_uuids in (phb_pair_a, phb_pair_b)

    def test_model_fits_one_gpu_colocated(self):
        # remaining=2 after PHB pair → colocated
        _, out, _ = run(self.TOPO, 70000)
        assert out["strategy"] == "colocated"

    def test_model_fits_one_gpu_pipeline(self):
        # PHB rank=30, n=2 → pipeline
        _, out, _ = run(self.TOPO, 70000)
        p = parallelism(out)
        assert p["mode"] == "pipeline"
        assert p["pipeline_parallel_size"] == 2

    def test_model_fits_one_gpu_no_gpu_idle(self):
        _, out, _ = run(self.TOPO, 70000)
        assert all_assigned_uuids(out) == set(self.UUIDS)

    def test_model_needs_two_gpus_colocated(self):
        _, out, _ = run(self.TOPO, 100000)
        assert out["strategy"] == "colocated"

    def test_model_needs_two_gpus_picks_phb_pair(self):
        _, out, _ = run(self.TOPO, 100000)
        llama_uuids = set(llama(out)["gpus"])
        phb_pair_a = {self.UUIDS[0], self.UUIDS[1]}
        phb_pair_b = {self.UUIDS[2], self.UUIDS[3]}
        assert llama_uuids in (phb_pair_a, phb_pair_b)

    def test_model_needs_two_gpus_pipeline(self):
        _, out, _ = run(self.TOPO, 100000)
        p = parallelism(out)
        assert p["mode"] == "pipeline"
        assert p["pipeline_parallel_size"] == 2

    def test_model_needs_three_gpus_colocated(self):
        _, out, _ = run(self.TOPO, 200000)
        assert out["strategy"] == "colocated"

    def test_model_needs_three_gpus_pipeline_cross_numa(self):
        _, out, _ = run(self.TOPO, 200000)
        p = parallelism(out)
        assert p["mode"] == "pipeline"
        assert p["pipeline_parallel_size"] == 3

    def test_model_too_large_errors(self):
        rc, _, stderr = run(self.TOPO, 400000)
        assert rc == 1
        assert "exceeds" in stderr.lower()


# ── 4 GPU — SYS-separated NVLink pairs ───────────────────────────────────────

class TestFourGpuSysNvPairs:
    """4x A100-80GB. GPU 0-1 NVLink rank=100, GPU 2-3 NODE rank=20, cross SYS rank=10."""
    TOPO = fixture_path("nvidia_smi_topo_matrix_4gpus_sys_separated_nv_pairs.json")
    UUIDS = [
        "GPU-00000000-0000-0000-0000-000000000000",
        "GPU-11111111-1111-1111-1111-111111111111",
        "GPU-22222222-2222-2222-2222-222222222222",
        "GPU-33333333-3333-3333-3333-333333333333",
    ]

    def test_model_fits_one_gpu_picks_nvlink_pair(self):
        # rank-first: NVLink pair rank=100 always wins
        _, out, _ = run(self.TOPO, 70000)
        assert set(llama(out)["gpus"]) == {self.UUIDS[0], self.UUIDS[1]}

    def test_model_fits_one_gpu_colocated(self):
        # remaining=2 → colocated
        _, out, _ = run(self.TOPO, 70000)
        assert out["strategy"] == "colocated"

    def test_model_fits_one_gpu_tensor(self):
        # NVLink rank=100, n=2 → tensor
        _, out, _ = run(self.TOPO, 70000)
        p = parallelism(out)
        assert p["mode"] == "tensor"
        assert p["tensor_parallel_size"] == 2
        assert p["pipeline_parallel_size"] == 1
        assert p["gpu_memory_utilization"] == 0.92

    def test_model_needs_two_gpus_picks_nvlink_pair(self):
        _, out, _ = run(self.TOPO, 100000)
        assert set(llama(out)["gpus"]) == {self.UUIDS[0], self.UUIDS[1]}

    def test_model_needs_two_gpus_tensor(self):
        _, out, _ = run(self.TOPO, 100000)
        p = parallelism(out)
        assert p["mode"] == "tensor"
        assert p["tensor_parallel_size"] == 2
        assert p["pipeline_parallel_size"] == 1
        assert p["gpu_memory_utilization"] == 0.92

    def test_model_needs_two_gpus_colocated(self):
        _, out, _ = run(self.TOPO, 100000)
        assert out["strategy"] == "colocated"

    def test_model_needs_three_gpus_cross_numa_pipeline(self):
        _, out, _ = run(self.TOPO, 200000)
        p = parallelism(out)
        assert p["mode"] == "pipeline"
        assert p["pipeline_parallel_size"] == 3

    def test_no_gpu_idle(self):
        for model_size in (70000, 100000, 200000):
            _, out, _ = run(self.TOPO, model_size)
            assert all_assigned_uuids(out) == set(self.UUIDS)


# ── 5 GPU — NV12 pair + 3 unlinked ───────────────────────────────────────────

class TestFiveGpuNv12WithMlx5:
    """5x A100-80GB. Only GPU 0-1 NV12 rank=100. All others rank=0."""
    TOPO = fixture_path("nvidia_smi_topo_matrix_5gpus_nv12_with_mlx5.json")
    UUIDS = [
        "GPU-00000000-0000-0000-0000-000000000000",
        "GPU-11111111-1111-1111-1111-111111111111",
        "GPU-22222222-2222-2222-2222-222222222222",
        "GPU-33333333-3333-3333-3333-333333333333",
        "GPU-44444444-4444-4444-4444-444444444444",
    ]

    def test_model_fits_one_gpu_picks_nvlink_pair(self):
        # rank-first: NVLink pair rank=100 always wins
        _, out, _ = run(self.TOPO, 70000)
        assert set(llama(out)["gpus"]) == {self.UUIDS[0], self.UUIDS[1]}

    def test_model_fits_one_gpu_dedicated(self):
        # remaining=3 exactly → dedicated, no extras back to llama
        _, out, _ = run(self.TOPO, 70000)
        assert out["strategy"] == "dedicated"

    def test_model_fits_one_gpu_llama_stays_2gpus(self):
        # remaining=3 → services each get 1, no extras push back
        _, out, _ = run(self.TOPO, 70000)
        assert len(llama(out)["gpus"]) == 2

    def test_model_fits_one_gpu_tensor(self):
        # NVLink rank=100, n=2 → tensor (no extra GPU degrading to pipeline)
        _, out, _ = run(self.TOPO, 70000)
        p = parallelism(out)
        assert p["mode"] == "tensor"
        assert p["tensor_parallel_size"] == 2
        assert p["pipeline_parallel_size"] == 1

    def test_model_fits_one_gpu_services_get_dedicated_gpus(self):
        _, out, _ = run(self.TOPO, 70000)
        svcs = out["services"]
        for name in ("whisper", "comfyui", "embeddings"):
            assert len(svcs[name]["gpus"]) == 1
        service_uuids = [svcs[n]["gpus"][0] for n in ("whisper", "comfyui", "embeddings")]
        assert len(set(service_uuids)) == 3

    def test_model_needs_nvlink_pair_tensor(self):
        _, out, _ = run(self.TOPO, 100000)
        assert set(llama(out)["gpus"]) == {self.UUIDS[0], self.UUIDS[1]}
        p = parallelism(out)
        assert p["mode"] == "tensor"
        assert p["tensor_parallel_size"] == 2
        assert p["pipeline_parallel_size"] == 1

    def test_model_needs_nvlink_pair_dedicated(self):
        _, out, _ = run(self.TOPO, 100000)
        assert out["strategy"] == "dedicated"

    def test_model_needs_nvlink_pair_no_extras_back(self):
        _, out, _ = run(self.TOPO, 100000)
        assert len(llama(out)["gpus"]) == 2

    def test_no_gpu_idle(self):
        for model_size in (70000, 100000):
            _, out, _ = run(self.TOPO, model_size)
            assert all_assigned_uuids(out) == set(self.UUIDS)


# ── 8 GPU — NV1/NV2 partial mesh ─────────────────────────────────────────────

class TestEightGpuPartialMesh:
    """8x V100-32GB. NV1=rank 0, NV2=rank 80, SYS=rank 10."""
    TOPO = fixture_path("nvidia_smi_topo_matrix_8gpus_nv1_nv2_partial_mesh.json")
    ALL_UUIDS = {f"GPU-{str(i)*8}-{str(i)*4}-{str(i)*4}-{str(i)*4}-{str(i)*12}" for i in range(8)}
    NV2_PAIRS = [
        {"GPU-00000000-0000-0000-0000-000000000000", "GPU-33333333-3333-3333-3333-333333333333"},
        {"GPU-00000000-0000-0000-0000-000000000000", "GPU-44444444-4444-4444-4444-444444444444"},
        {"GPU-11111111-1111-1111-1111-111111111111", "GPU-22222222-2222-2222-2222-222222222222"},
        {"GPU-11111111-1111-1111-1111-111111111111", "GPU-55555555-5555-5555-5555-555555555555"},
        {"GPU-22222222-2222-2222-2222-222222222222", "GPU-33333333-3333-3333-3333-333333333333"},
        {"GPU-44444444-4444-4444-4444-444444444444", "GPU-77777777-7777-7777-7777-777777777777"},
        {"GPU-55555555-5555-5555-5555-555555555555", "GPU-66666666-6666-6666-6666-666666666666"},
        {"GPU-66666666-6666-6666-6666-666666666666", "GPU-77777777-7777-7777-7777-777777777777"},
    ]

    def test_model_fits_one_gpu_dedicated(self):
        # remaining=6 → dedicated (3 to services, 3 extras back to llama)
        _, out, _ = run(self.TOPO, 20000)
        assert out["strategy"] == "dedicated"

    def test_model_fits_one_gpu_picks_nv2_pair(self):
        _, out, _ = run(self.TOPO, 20000)
        initial_pair = set(llama(out)["gpus"][:2])
        assert any(initial_pair == p for p in self.NV2_PAIRS)

    def test_model_fits_one_gpu_extras_back_to_llama(self):
        # remaining=6: services get 3, extras 3 → llama total=5
        _, out, _ = run(self.TOPO, 20000)
        assert len(llama(out)["gpus"]) == 5

    def test_model_fits_one_gpu_pipeline(self):
        # extras degrade min_rank → pipeline
        _, out, _ = run(self.TOPO, 20000)
        assert parallelism(out)["mode"] == "pipeline"

    def test_model_needs_nv2_pair_picks_nv2_pair(self):
        _, out, _ = run(self.TOPO, 50000)
        initial_pair = set(llama(out)["gpus"][:2])
        assert any(initial_pair == p for p in self.NV2_PAIRS)

    def test_model_needs_nv2_pair_extras_make_pipeline(self):
        _, out, _ = run(self.TOPO, 50000)
        assert parallelism(out)["mode"] == "pipeline"

    def test_no_gpu_idle(self):
        for model_size in (20000, 50000):
            _, out, _ = run(self.TOPO, model_size)
            assert all_assigned_uuids(out) == self.ALL_UUIDS

    def test_model_too_large_errors(self):
        rc, _, stderr = run(self.TOPO, 300000)
        assert rc == 1
        assert "exceeds" in stderr.lower()


# ── 8 GPU — NV12 full mesh ────────────────────────────────────────────────────

class TestEightGpuNv12FullMesh:
    """8x A100-80GB. All pairs NV12 rank=100."""
    TOPO = fixture_path("nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.json")
    ALL_UUIDS = {f"GPU-{str(i)*8}-{str(i)*4}-{str(i)*4}-{str(i)*4}-{str(i)*12}" for i in range(8)}

    def test_model_fits_one_gpu_dedicated(self):
        _, out, _ = run(self.TOPO, 70000)
        assert out["strategy"] == "dedicated"

    def test_model_fits_one_gpu_services_get_dedicated_gpus(self):
        _, out, _ = run(self.TOPO, 70000)
        svcs = out["services"]
        uuids = [svcs[n]["gpus"][0] for n in ("whisper", "comfyui", "embeddings")]
        assert len(set(uuids)) == 3

    def test_model_fits_one_gpu_extras_back_to_llama_nvlink(self):
        # NVLink pair wins, remaining=6: 3 to services, 3 extras → llama=5 GPUs, hybrid
        _, out, _ = run(self.TOPO, 70000)
        p = parallelism(out)
        assert p["mode"] == "hybrid"
        assert p["gpu_memory_utilization"] == 0.93

    def test_model_fits_one_gpu_llama_5gpus(self):
        _, out, _ = run(self.TOPO, 70000)
        assert len(llama(out)["gpus"]) == 5

    def test_model_needs_two_gpus_extras_back_to_llama(self):
        _, out, _ = run(self.TOPO, 100000)
        assert len(llama(out)["gpus"]) == 5

    def test_model_needs_two_gpus_hybrid_nvlink(self):
        _, out, _ = run(self.TOPO, 100000)
        p = parallelism(out)
        assert p["mode"] == "hybrid"
        assert p["tensor_parallel_size"] == 2
        assert p["pipeline_parallel_size"] == 2
        assert p["gpu_memory_utilization"] == 0.93

    def test_model_needs_five_gpus_no_extras(self):
        # 350GB needs 5 GPUs. remaining=3 exactly → no extras → llama has 5 GPUs
        _, out, _ = run(self.TOPO, 350000)
        assert len(llama(out)["gpus"]) == 5
        assert out["strategy"] == "dedicated"

    def test_model_needs_five_gpus_hybrid(self):
        _, out, _ = run(self.TOPO, 350000)
        p = parallelism(out)
        assert p["mode"] == "hybrid"
        assert p["tensor_parallel_size"] == 2
        assert p["pipeline_parallel_size"] == 2

    def test_model_needs_five_gpus_services_dedicated(self):
        _, out, _ = run(self.TOPO, 350000)
        svcs = out["services"]
        uuids = [svcs[n]["gpus"][0] for n in ("whisper", "comfyui", "embeddings")]
        assert len(set(uuids)) == 3

    def test_no_gpu_idle(self):
        for model_size in (70000, 100000, 350000):
            _, out, _ = run(self.TOPO, model_size)
            assert all_assigned_uuids(out) == self.ALL_UUIDS

    def test_model_too_large_errors(self):
        rc, _, stderr = run(self.TOPO, 700000)
        assert rc == 1
        assert "exceeds" in stderr.lower()


# ── 8 GPU — NV12 full mesh with NUMA annotation ───────────────────────────────

class TestEightGpuNv12FullMeshWithNuma:
    """NUMA annotation should not affect results."""
    TOPO_WITH_NUMA    = fixture_path("nvidia_smi_topo_matrix_8gpus_nv12_full_mesh_with_numa_id.json")
    TOPO_WITHOUT_NUMA = fixture_path("nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.json")

    def test_numa_annotation_does_not_affect_strategy(self):
        _, out_numa, _    = run(self.TOPO_WITH_NUMA, 100000)
        _, out_no_numa, _ = run(self.TOPO_WITHOUT_NUMA, 100000)
        assert out_numa["strategy"] == out_no_numa["strategy"]

    def test_numa_annotation_does_not_affect_parallelism_mode(self):
        _, out_numa, _    = run(self.TOPO_WITH_NUMA, 100000)
        _, out_no_numa, _ = run(self.TOPO_WITHOUT_NUMA, 100000)
        assert parallelism(out_numa)["mode"] == parallelism(out_no_numa)["mode"]

    def test_numa_annotation_does_not_affect_llama_gpu_count(self):
        _, out_numa, _    = run(self.TOPO_WITH_NUMA, 100000)
        _, out_no_numa, _ = run(self.TOPO_WITHOUT_NUMA, 100000)
        assert len(llama(out_numa)["gpus"]) == len(llama(out_no_numa)["gpus"])


# ── Output schema ─────────────────────────────────────────────────────────────

class TestOutputSchema:
    TOPO = fixture_path("nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.json")

    def test_version_field_present(self):
        _, out, _ = run(self.TOPO, 100000)
        assert out["version"] == "1.0"

    def test_strategy_field_present(self):
        _, out, _ = run(self.TOPO, 100000)
        assert out["strategy"] in ("single", "dedicated", "colocated", "user-defined")

    def test_all_four_services_present(self):
        _, out, _ = run(self.TOPO, 100000)
        for svc in ("llama_server", "whisper", "comfyui", "embeddings"):
            assert svc in out["services"]

    def test_gpus_always_a_list(self):
        _, out, _ = run(self.TOPO, 100000)
        for svc in out["services"].values():
            assert isinstance(svc["gpus"], list)
            assert len(svc["gpus"]) >= 1

    def test_non_llama_services_have_no_parallelism_block(self):
        _, out, _ = run(self.TOPO, 100000)
        for name in ("whisper", "comfyui", "embeddings"):
            assert "parallelism" not in out["services"][name]

    def test_llama_always_has_parallelism_block(self):
        _, out, _ = run(self.TOPO, 100000)
        p = parallelism(out)
        assert "mode" in p
        assert "tensor_parallel_size" in p
        assert "pipeline_parallel_size" in p
        assert "gpu_memory_utilization" in p

    def test_tensor_split_absent_when_homogeneous(self):
        _, out, _ = run(self.TOPO, 100000)
        assert "tensor_split" not in parallelism(out)

    def test_gpu_uuids_are_strings(self):
        _, out, _ = run(self.TOPO, 100000)
        for svc in out["services"].values():
            for uuid in svc["gpus"]:
                assert isinstance(uuid, str)
                assert uuid.startswith("GPU-")


# ── Parallelism mode selection ────────────────────────────────────────────────

class TestParallelismModeSelection:

    def test_nvlink_two_gpus_tensor(self):
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_4gpus_sys_separated_nv_pairs.json"), 100000)
        assert parallelism(out)["mode"] == "tensor"

    def test_pcie_phb_two_gpus_pipeline(self):
        # PHB rank=30 → pipeline
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_4gpus_soc.json"), 100000)
        assert parallelism(out)["mode"] == "pipeline"

    def test_cross_numa_three_gpus_pipeline(self):
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_4gpus_soc.json"), 200000)
        p = parallelism(out)
        assert p["mode"] == "pipeline"
        assert p["pipeline_parallel_size"] == 3

    def test_nvlink_full_mesh_five_gpus_hybrid(self):
        # NV12 full mesh, extras push back → 5 GPUs → hybrid
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.json"), 100000)
        assert parallelism(out)["mode"] == "hybrid"

    def test_mem_util_none_is_095(self):
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_1gpu_pcie.json"), 20000)
        assert parallelism(out)["gpu_memory_utilization"] == 0.95

    def test_mem_util_tensor_is_092(self):
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_4gpus_sys_separated_nv_pairs.json"), 100000)
        assert parallelism(out)["gpu_memory_utilization"] == 0.92

    def test_mem_util_hybrid_is_093(self):
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_8gpus_nv12_full_mesh.json"), 100000)
        assert parallelism(out)["gpu_memory_utilization"] == 0.93

    def test_mem_util_pipeline_is_095(self):
        _, out, _ = run(fixture_path("nvidia_smi_topo_matrix_4gpus_soc.json"), 100000)
        assert parallelism(out)["gpu_memory_utilization"] == 0.95
