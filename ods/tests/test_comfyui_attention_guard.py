"""The ComfyUI image's attention guard, with PyTorch replaced by a stand-in.

On Workstation06's GB300 (sm_103, 2026-10-26) the image's PyTorch 2.11.0+cu128
(built for sm_80, sm_90, sm_100 and sm_120) failed every generation in cuDNN
attention ("No valid execution plans built"); flash, memory-efficient and math
attention all ran. The guard turns cuDNN attention off on such a GPU only.
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "extensions/services/comfyui/ods_attention_guard/__init__.py"
CU128_ARCHES = ["sm_80", "sm_90", "sm_100", "sm_120"]


def load_guard(available, capability=(0, 0), arches=CU128_ARCHES):
    calls = []
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: available,
                                       get_device_capability=lambda: capability,
                                       get_arch_list=lambda: list(arches))
    torch.backends = types.SimpleNamespace(cuda=types.SimpleNamespace(enable_cudnn_sdp=calls.append))
    previous = sys.modules.get("torch")
    sys.modules["torch"] = torch
    try:
        spec = importlib.util.spec_from_file_location("ods_attention_guard_under_test", GUARD)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        if previous is None:
            sys.modules.pop("torch")
        else:
            sys.modules["torch"] = previous
    return module, calls


class AttentionGuardTests(unittest.TestCase):
    def test_turns_cudnn_attention_off_on_a_gpu_the_build_lacks(self):
        for capability in ((10, 3), (12, 1)):  # GB300, GB10
            with self.subTest(capability=capability):
                _, calls = load_guard(True, capability)
                self.assertEqual(calls, [False])

    def test_leaves_a_gpu_the_build_targets_alone(self):
        for capability in ((12, 0), (10, 0), (9, 0), (8, 0)):
            with self.subTest(capability=capability):
                _, calls = load_guard(True, capability)
                self.assertEqual(calls, [])

    def test_is_inert_without_a_gpu(self):
        # The image's build check loads every custom node on the CPU.
        _, calls = load_guard(False)
        self.assertEqual(calls, [])

    def test_loads_as_a_custom_node_without_nodes(self):
        module, _ = load_guard(False)
        self.assertEqual((module.NODE_CLASS_MAPPINGS, module.NODE_DISPLAY_NAME_MAPPINGS), ({}, {}))


if __name__ == "__main__":
    unittest.main()
