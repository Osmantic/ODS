"""Negative cases for independent runtime release pins and retained custody."""
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('llama_compat', Path(__file__).with_name('test-llama-cpp-compat.py'))
CONTRACT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CONTRACT)
SOURCE = CONTRACT.ROOT_DIR
ENV_SPEC = importlib.util.spec_from_file_location('llama_spec', Path(__file__).with_name('test-llama-spec-default.py'))
ENV_CONTRACT = importlib.util.module_from_spec(ENV_SPEC)
ENV_SPEC.loader.exec_module(ENV_CONTRACT)


class BackendPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        paths = set(CONTRACT.NVIDIA_COPIES + CONTRACT.CPU_COPIES + CONTRACT.AMD_COPIES)
        paths.update({
            'docker-compose.nvidia.yml', 'docker-compose.cpu.yml', 'docker-compose.amd.yml',
            'docker-compose.amd-rocm.yml', 'docker-compose.intel.yml', 'docker-compose.apple.yml',
            'docker-compose.arc.yml', 'images/llama-sycl/Dockerfile',
            'installers/windows/lib/constants.ps1', 'installers/windows/lib/native-llama-runtime.ps1',
            'installers/phases/06-directories.sh',
        })
        for name in paths:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, target)
        CONTRACT.ROOT_DIR = self.root
        ENV_CONTRACT.ROOT_DIR = self.root

    def tearDown(self):
        CONTRACT.ROOT_DIR = SOURCE
        ENV_CONTRACT.ROOT_DIR = SOURCE
        self.temp.cleanup()

    def errors(self):
        errors = []
        CONTRACT.check_pins(errors)
        CONTRACT.check_other_backends(errors)
        return errors

    def replace(self, name, old, new):
        path = self.root / name
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def test_independent_reviewed_versions_pass(self):
        self.assertNotEqual(CONTRACT.BACKEND_BUILDS['nvidia'], CONTRACT.BACKEND_BUILDS['cpu'])
        self.assertEqual([], self.errors())

    def test_each_docker_backend_rejects_unreviewed_build(self):
        for backend, name in (
            ('nvidia', 'docker-compose.nvidia.yml'), ('cpu', 'docker-compose.cpu.yml'),
            ('amd-vulkan', 'docker-compose.amd.yml'), ('amd-rocm', 'docker-compose.amd-rocm.yml'),
            ('intel', 'docker-compose.intel.yml'), ('apple', 'docker-compose.apple.yml'),
        ):
            with self.subTest(backend=backend):
                original = (self.root / name).read_bytes()
                self.replace(name, CONTRACT.BACKEND_BUILDS[backend], 'b99999')
                self.assertTrue(any(f'{backend} release policy' in error for error in self.errors()))
                (self.root / name).write_bytes(original)

    def test_digest_cannot_be_removed(self):
        ref = CONTRACT.compose_default('docker-compose.nvidia.yml')
        self.replace('docker-compose.nvidia.yml', ref, ref.split('@')[0])
        self.assertTrue(any('without @sha256 digest' in error for error in self.errors()))

    def test_default_copy_cannot_drift(self):
        ref = CONTRACT.compose_default('docker-compose.nvidia.yml')
        self.replace('installers/phases/08-images.sh', ref, ref.split('@')[0] + '@sha256:' + '0' * 64)
        errors = self.errors()
        self.assertTrue(any('does not repeat the NVIDIA' in error for error in errors))
        self.assertTrue(any('different digests' in error for error in errors))

    def test_arc_commit_custody_remains_required(self):
        text = (self.root / 'images/llama-sycl/Dockerfile').read_text()
        commit = next(line.split('=', 1)[1] for line in text.splitlines() if line.startswith('ARG LLAMA_COMMIT='))
        self.replace('images/llama-sycl/Dockerfile', commit, '0' * 40)
        self.assertTrue(any('same full commit SHA' in error for error in self.errors()))

    def test_windows_native_tag_cannot_follow_cuda(self):
        self.replace('installers/windows/lib/tier-map.ps1', '$runtimeTag = "b9014"', '$runtimeTag = "b11429"')
        self.assertTrue(any('Windows native runtime tag b11429 differs' in error for error in self.errors()))

    def test_windows_archive_hash_stays_bound_to_lock(self):
        path = self.root / 'config/backends/amd.json'
        data = json.loads(path.read_text())
        data['runtime']['llama_server']['windows']['sha256'] = '0' * 64
        path.write_text(json.dumps(data))
        self.assertTrue(any('must match the dependency-lock archive' in error for error in self.errors()))

    def test_each_spec_default_has_reviewed_argument_contract(self):
        errors = []
        self.assertEqual({'docker-compose.nvidia.yml': 11429, 'docker-compose.cpu.yml': 9014},
                         ENV_CONTRACT.default_pinned_builds(errors))
        self.assertEqual([], errors)
        self.replace('docker-compose.cpu.yml', 'b9014', 'b99999')
        ENV_CONTRACT.default_pinned_builds(errors)
        self.assertTrue(any('no env-name/--spec-type sets' in error for error in errors))

    def test_removed_checkpoint_setting_cannot_get_new_default(self):
        errors = []
        ENV_CONTRACT.check_env_names('nvidia', 11429, {'LLAMA_ARG_CHECKPOINT_EVERY_NT': None}, errors)
        self.assertEqual([], errors)
        ENV_CONTRACT.check_env_names('nvidia', 11429, {'LLAMA_ARG_CHECKPOINT_EVERY_NT': '-1'}, errors)
        self.assertTrue(any('removed LLAMA_ARG_CHECKPOINT_EVERY_NT' in error for error in errors))

    def test_unknown_argument_and_old_backend_new_default_rejected(self):
        errors = []
        ENV_CONTRACT.check_env_names('nvidia', 11429, {'LLAMA_ARG_UNREVIEWED': None}, errors)
        self.assertTrue(any('does not read LLAMA_ARG_UNREVIEWED' in error for error in errors))
        errors = []
        ENV_CONTRACT.check_env_names('cpu', 9014, {'LLAMA_ARG_CHECKPOINT_MIN_SPACING_NT': '1024'}, errors)
        self.assertTrue(any('must stay a bare pass-through' in error for error in errors))


if __name__ == '__main__':
    unittest.main()
