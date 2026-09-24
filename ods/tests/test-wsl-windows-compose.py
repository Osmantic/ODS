"""Run the actual Compose resolver against an isolated installation fixture."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ComposePlacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ods-windows-compose-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in ('docker-compose.base.yml', 'docker-compose.lemonade-external.yml',
                     'docker-compose.nvidia.yml', 'docker-compose.amd.yml'):
            shutil.copyfile(ROOT / name, self.root / name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(
            ('ODS_', 'AMD_INFERENCE_', 'LEMONADE_', 'EXTERNAL_LLM_', 'PIXEL_'))}
        self.env.update(AMD_INFERENCE_RUNTIME_MODE='wsl-windows-lemonade',
                        AMD_INFERENCE_RUNTIME='lemonade', AMD_INFERENCE_MANAGED='true',
                        AMD_INFERENCE_LOCATION='host', LEMONADE_EXTERNAL='false')

    def resolve(self, backend='amd', mode='lemonade'):
        result = subprocess.run(['bash', str(ROOT / 'scripts/resolve-compose-stack.sh'),
            '--script-dir', str(self.root), '--gpu-backend', backend, '--ods-mode', mode,
            '--tier', '2'], env=self.env, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_managed_windows_uses_host_transport_without_linux_gpu_container(self):
        result = self.resolve()
        self.assertIn('docker-compose.lemonade-external.yml', result)
        self.assertNotIn('docker-compose.amd.yml', result)
        self.assertNotIn('docker-compose.cloud.yml', result)

    def test_nvidia_keeps_cuda_even_with_stale_amd_mode(self):
        result = self.resolve(backend='nvidia', mode='local')
        self.assertIn('docker-compose.nvidia.yml', result)
        self.assertNotIn('docker-compose.lemonade-external.yml', result)

    def test_linux_amd_keeps_container_runtime(self):
        self.env['AMD_INFERENCE_RUNTIME_MODE'] = 'linux-container'
        self.env['AMD_INFERENCE_LOCATION'] = 'container'
        result = self.resolve(mode='local')
        self.assertIn('docker-compose.amd.yml', result)
        self.assertNotIn('docker-compose.lemonade-external.yml', result)


if __name__ == '__main__':
    unittest.main()
