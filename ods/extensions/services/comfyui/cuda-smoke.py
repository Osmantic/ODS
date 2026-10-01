"""Check a real CUDA kernel in the running standalone ComfyUI container."""

import torch

if not torch.cuda.is_available():
    raise SystemExit("CUDA is unavailable inside standalone ComfyUI")

result = torch.ones(8, device="cuda").sum().item()
if result != 8:
    raise SystemExit("CUDA kernel returned an unexpected result")

print(f"CUDA ready: {torch.cuda.get_device_name(0)}")
