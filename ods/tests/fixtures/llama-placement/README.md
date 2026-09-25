# llama-server placement fixtures

Excerpts of real llama-server load logs from the qualification fleet, used by
`tests/test-llama-gpu-residency.py` and `tests/test-ods-doctor-gpu-residency.sh`.
Lines were cut from the original logs, not edited, except that a home
directory in the Mac model path was replaced with `/Users/ods/ods`.

| File | Host and configuration | Placement |
|---|---|---|
| `laptop-rtx5070-9b-64k-partial-b9014.txt` | RTX 5070 Laptop (8 GB, WSL2), Qwen3.5-9B Q4_K_M, 64K context, q8_0 KV, `--n-gpu-layers auto`, llama.cpp b9014 | 29/33: the default 1024 MiB fit margin moves 4 layers to the CPU |
| `laptop-rtx5070-9b-64k-fit512-resident-b9014.txt` | Same laptop and model with `--fit-target 512 -ub 256` | 33/33 |
| `mac-mini-m4-9b-metal-resident-b8210.txt` | Mac mini M4 (16 GB), native Metal, llama.cpp b8210 | 33/33 |
| `tower-rtx5090-27b-64k-resident-docker-timestamps-b9014.txt` | RTX 5090, Qwen3.5-27B, `docker logs --timestamps` | 65/65 |
| `tower2-2xrtxpro6000-coder-next-128k-resident-docker-timestamps-b9014.txt` | 2x RTX PRO 6000, qwen3-coder-next, 128K, layer split | 49/49 |
