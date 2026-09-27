# llama-server placement fixtures

Excerpts of real llama-server load logs from the qualification fleet, used by
`tests/test-llama-gpu-residency.py`, `tests/test-ods-doctor-gpu-residency.sh`
and `tests/contracts/test-llama-placement-log.py`. Lines were cut from the
original logs, not edited, except that a home directory in the Mac model path
was replaced with `/Users/ods/ods`.

| File | Host and configuration | Placement |
|---|---|---|
| `laptop-rtx5070-9b-64k-partial-b9014.txt` | RTX 5070 Laptop (8 GB, WSL2), Qwen3.5-9B Q4_K_M, 64K context, q8_0 KV, `--n-gpu-layers auto`, llama.cpp b9014 | 29/33: the default 1024 MiB fit margin moves 4 layers to the CPU |
| `laptop-rtx5070-9b-64k-fit512-resident-b9014.txt` | Same laptop and model with `--fit-target 512 -ub 256` | 33/33 |
| `mac-mini-m4-9b-metal-resident-b8210.txt` | Mac mini M4 (16 GB), native Metal, llama.cpp b8210 | 33/33 |
| `tower-rtx5090-27b-32k-resident-docker-timestamps-b9014.txt` | RTX 5090, Qwen3.5-27B, 32K context, `docker logs --timestamps` | 65/65 |
| `tower2-2xrtxpro6000-coder-next-128k-resident-docker-timestamps-b9014.txt` | 2x RTX PRO 6000, qwen3-coder-next, 128K, layer split | 49/49 |
| `tower2-rtxpro6000-qwen35-2b-resident-b9014.txt` | RTX PRO 6000 (GPU 0), Qwen3.5-2B Q4_K_M, 64K, `server-cuda-b9014` image run with `-e LLAMA_ARG_LOG_VERBOSITY=4` as ODS passes it (b9014 ignores that name). A complete load: start, placement, `model loaded`. | 25/25 |
| `tower2-rtxpro6000-qwen35-2b-resident-lv4-b11146.txt` | Same model and GPU, `server-cuda-v0.5.0` (build 11146) with `-e LLAMA_ARG_LOG_VERBOSITY=4` | 25/25 |
| `tower2-rtxpro6000-qwen35-2b-default-verbosity-b11146.txt` | Same, at llama.cpp's default verbosity 3: from b9151 the model loads without any placement line | not logged |

The three `tower2-rtxpro6000-qwen35-2b-*` logs were captured on 2026-09-25 in
throwaway containers (read-only model mount, `--n-gpu-layers auto --ctx-size
65536`). Image IDs (`docker inspect .Image`): b9014
`sha256:fcf285820892e7ce3218379634e3590826fc697e8b6745b9392072462e355c4f`,
v0.5.0 `sha256:52192a23c7b258044463aebba78826cdff39a7393b713edc313324b343a7b836`.

When a llama.cpp pin moves, capture a load log from the new build the same
way, add it here and register it in
`tests/contracts/test-llama-placement-log.py`.
