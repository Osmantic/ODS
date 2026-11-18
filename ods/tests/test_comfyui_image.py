"""Build the NVIDIA ComfyUI image and check what it ships (CI only).

Set ODS_TEST_COMFYUI_IMAGE=1 to run it. It needs Docker and network access but
no GPU: the Dockerfile's own build check already loaded every custom node on
the CPU. ODS_TEST_COMFYUI_IIDFILE names where the built image id is written.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

CONTEXT = Path(__file__).resolve().parents[1] / "extensions/services/comfyui"
NODES = ("ComfyUI-GGUF", "ComfyUI-KJNodes", "ComfyUI-LTXVideo", "ComfyUI-VideoHelperSuite", "ods_attention_guard")


@unittest.skipUnless(os.environ.get("ODS_TEST_COMFYUI_IMAGE") == "1",
                     "set ODS_TEST_COMFYUI_IMAGE=1 to build the ComfyUI image")
class ComfyUIImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        iidfile = os.environ.get("ODS_TEST_COMFYUI_IIDFILE")
        if not iidfile:
            iidfile = str(Path(tempfile.mkdtemp(prefix="comfyui-image-")) / "image-id")
        subprocess.run(["docker", "build", "--iidfile", iidfile,
                        "-f", str(CONTEXT / "Dockerfile"), str(CONTEXT)], check=True)
        cls.image = Path(iidfile).read_text(encoding="utf-8").strip()

    def python(self, code):
        result = subprocess.run(
            ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python3", self.image, "-c", code],
            check=True, capture_output=True, text=True)
        return result.stdout.strip()

    def test_pinned_torch_runs_without_a_gpu(self):
        self.assertEqual(self.python("import torch; print(torch.__version__, torch.version.cuda)"),
                         "2.11.0+cu128 12.8")

    def test_runs_as_the_unprivileged_comfyui_user(self):
        result = subprocess.run(["docker", "image", "inspect", "--format", "{{.Config.User}}", self.image],
                                check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), "comfyui")

    def test_ships_the_pinned_nodes_and_no_manager(self):
        installed = self.python("import os; print(' '.join(sorted(os.listdir('/opt/comfyui/custom_nodes'))))").split()
        for node in NODES:
            self.assertIn(node, installed)
        self.assertNotIn("ComfyUI-Manager", installed)

    def test_attention_guard_loads_and_is_inert_without_a_gpu(self):
        # It turns cuDNN attention off only on a GPU the PyTorch build lacks (GB300).
        self.assertEqual(self.python(
            "import sys; sys.path.insert(0, '/opt/comfyui/custom_nodes'); import torch, ods_attention_guard; "
            "print(torch.backends.cuda.cudnn_sdp_enabled())"), "True")

    def test_build_check_left_no_user_directory(self):
        # startup.sh migrates an image-side user/ into the owner's mount.
        self.assertEqual(self.python("import os; print(os.path.exists('/opt/comfyui/user'))"), "False")


if __name__ == "__main__":
    unittest.main()
