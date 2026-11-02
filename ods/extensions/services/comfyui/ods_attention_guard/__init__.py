"""ODS: keep PyTorch attention off cuDNN on a GPU this PyTorch build has no kernels for.

The image's PyTorch (2.11.0+cu128) is built for sm_80, sm_90, sm_100 and
sm_120. On a GB300 (sm_103) its cuDNN attention found no execution plan
("cuDNN Frontend error: No valid execution plans built") and every image
generation failed, while the flash, memory-efficient and math kernels all ran
(Workstation06, 2026-10-26). PyTorch prefers cuDNN attention on GPUs this new,
so it is turned off on a GPU missing from the build's architecture list and
nowhere else.
"""
import logging

import torch

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

if torch.cuda.is_available():
    major, minor = torch.cuda.get_device_capability()
    if f"sm_{major}{minor}" not in torch.cuda.get_arch_list():
        torch.backends.cuda.enable_cudnn_sdp(False)
        logging.warning("ODS: cuDNN attention is off: this PyTorch build has no sm_%d%d kernels.", major, minor)
