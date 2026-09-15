"""Regression test for aggregate_gpu_details metric handling and bounds clamping."""
from gpu import aggregate_gpu_details
from models import IndividualGPU


def test_aggregate_gpu_details_clamps_utilization_and_handles_unavailable_metrics():
    gpu1 = IndividualGPU(
        index=0,
        uuid="gpu-0",
        name="RTX 4090",
        memory_used_mb=4096,
        memory_total_mb=24576,
        memory_percent=16.7,
        utilization_percent=0,
        temperature_c=0,
        power_w=None,
        memory_type="discrete",
        assigned_services=[],
        memory_usage_available=True,
        utilization_available=False,
        temperature_available=False,
    )
    gpu2 = IndividualGPU(
        index=1,
        uuid="gpu-1",
        name="RTX 4090",
        memory_used_mb=8192,
        memory_total_mb=24576,
        memory_percent=33.3,
        utilization_percent=120,  # Out-of-bound driver metric
        temperature_c=65,
        power_w=250.0,
        memory_type="discrete",
        assigned_services=[],
        memory_usage_available=True,
        utilization_available=True,
        temperature_available=True,
    )

    agg = aggregate_gpu_details([gpu1, gpu2], backend="nvidia")
    assert agg.memory_used_mb == 12288
    assert agg.memory_total_mb == 49152
    # Utilization should be clamped to 100
    assert agg.utilization_percent == 100
    # Temperature should reflect available reading
    assert agg.temperature_c == 65
    assert agg.power_w == 250.0
