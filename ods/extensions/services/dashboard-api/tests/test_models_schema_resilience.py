"""Unit test suite for Pydantic response models schema resilience."""

import unittest
from models import GPUInfo, ServiceStatus


class TestModelsSchemaResilience(unittest.TestCase):
    def test_gpu_info_defaults(self):
        gpu = GPUInfo(
            name="NVIDIA RTX 4090",
            memory_used_mb=4096,
            memory_total_mb=24576,
            memory_percent=16.67,
            utilization_percent=45,
            temperature_c=65,
        )
        self.assertEqual(gpu.name, "NVIDIA RTX 4090")
        self.assertEqual(gpu.gpu_count, 1)

    def test_service_status_fields(self):
        srv = ServiceStatus(
            id="dashboard-api",
            name="Dashboard API",
            port=8000,
            external_port=8000,
            status="running",
        )
        self.assertEqual(srv.id, "dashboard-api")
        self.assertEqual(srv.port, 8000)
        self.assertEqual(srv.external_port, 8000)


if __name__ == "__main__":
    unittest.main()
