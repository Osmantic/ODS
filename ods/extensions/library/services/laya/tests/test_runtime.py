import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location('laya_runtime', Path(__file__).resolve().parents[1] / 'runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class AllocationError(RuntimeError):
    pass


def torch_fixture(free_gib=7, total_gib=16, available=True):
    return SimpleNamespace(cuda=SimpleNamespace(
        is_available=Mock(return_value=available),
        mem_get_info=Mock(return_value=(free_gib * 1024**3, total_gib * 1024**3)),
        set_per_process_memory_fraction=Mock(), empty_cache=Mock(), OutOfMemoryError=AllocationError))


class RuntimeTest(unittest.TestCase):
    def test_cpu_selection_never_initializes_cuda_and_invalid_modes_fail(self):
        torch = torch_fixture()
        self.assertEqual(runtime.select_device(torch, {'LAYA_ACCELERATION': 'cpu'})['device'], 'cpu')
        torch.cuda.is_available.assert_not_called()
        for value in ['nvidia', '', 'AUTO', '${GPU}', 'cuda:0']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                runtime.select_device(torch, {'LAYA_ACCELERATION': value})

    def test_auto_uses_cpu_when_cuda_is_absent_or_shared_memory_is_low(self):
        for torch, reason in [(torch_fixture(available=False), 'cuda-unavailable'),
                              (torch_fixture(free_gib=3), 'insufficient-cuda-headroom'),
                              (torch_fixture(total_gib=4), 'insufficient-cuda-headroom')]:
            with self.subTest(reason=reason):
                self.assertEqual(runtime.select_device(torch, {}),
                                 {'device': 'cpu', 'allocatorBytes': 0, 'reason': reason})
                torch.cuda.set_per_process_memory_fraction.assert_not_called()
                with self.assertRaisesRegex(RuntimeError, 'Requested CUDA'):
                    runtime.select_device(torch, {'LAYA_ACCELERATION': 'cuda'})

    def test_allocator_budget_respects_fraction_absolute_limit_and_headroom(self):
        for free, total, expected in [(7, 16, 4), (7, 24, 4), (4, 16, 2), (6, 8, 2)]:
            with self.subTest(free=free, total=total):
                torch = torch_fixture(free, total)
                result = runtime.select_device(torch, {})
                self.assertEqual(result['device'], 'cuda:0')
                self.assertEqual(result['allocatorBytes'], expected * 1024**3)
                torch.cuda.set_per_process_memory_fraction.assert_called_once_with(expected / total, 0)

    def test_driver_initialization_failure_falls_back_only_in_auto(self):
        torch = torch_fixture()
        torch.cuda.mem_get_info.side_effect = RuntimeError('driver initialization')
        self.assertEqual(runtime.select_device(torch, {})['reason'], 'cuda-initialization-failed')
        with self.assertRaisesRegex(RuntimeError, 'Requested CUDA'):
            runtime.select_device(torch, {'LAYA_ACCELERATION': 'cuda'})

    def test_budget_precedes_model_loading_and_all_startup_inference(self):
        torch = torch_fixture()
        env = {}
        router = Mock()

        def build():
            torch.cuda.set_per_process_memory_fraction.assert_called_once()
            self.assertEqual(env['LAYA_DEVICE'], 'cuda:0')
            self.assertEqual(env['LAYA_PRELOAD'], '0')
            self.assertEqual(env['LAYA_MAX_LOADED'], '1')
            self.assertEqual(env['LAYA_IDLE_UNLOAD_SECONDS'], '60')
            return router

        qualify = Mock()
        self.assertIs(runtime.build_qualified_router(build, qualify, torch=torch, env=env), router)
        qualify.assert_called_once_with(router)

    def test_startup_oom_releases_gpu_and_qualifies_cpu_once(self):
        torch, env = torch_fixture(), {}
        first, second = Mock(), Mock()
        build = Mock(side_effect=[first, second])
        qualify = Mock(side_effect=[AllocationError(), None])
        self.assertIs(runtime.build_qualified_router(build, qualify, torch=torch, env=env), second)
        first.unload.assert_called_once()
        torch.cuda.empty_cache.assert_called_once()
        self.assertEqual(env['LAYA_DEVICE'], 'cpu')
        self.assertEqual(build.call_count, 2)
        self.assertEqual(qualify.call_count, 2)

    def test_model_defects_and_explicit_cuda_oom_are_not_retried(self):
        for mode, error in [('auto', ValueError('invalid typed result')),
                            ('auto', RuntimeError('kernel defect')),
                            ('cuda', AllocationError())]:
            with self.subTest(mode=mode, error=error):
                build, qualify = Mock(return_value=Mock()), Mock(side_effect=error)
                with self.assertRaises(type(error)):
                    runtime.build_qualified_router(build, qualify, torch=torch_fixture(),
                                                   env={'LAYA_ACCELERATION': mode})
                self.assertEqual(build.call_count, 1)


if __name__ == '__main__':
    unittest.main()
