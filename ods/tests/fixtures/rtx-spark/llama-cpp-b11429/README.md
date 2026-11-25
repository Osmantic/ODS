# llama.cpp b11429 `win-cuda-13.4-arm64` fixtures (RTX Spark)

Captured on an RTX Spark (Windows 11 Arm64, NVIDIA N1X, compute capability
12.1, driver 616.62) on 2026-10-17, read-only. Host names, user names, paths,
SIDs, GPU and NPU UUIDs and the engine's details are removed. Used by
`tests/contracts/test-windows-native-llama-cuda-runtime.ps1`.

| File | Source |
|---|---|
| `llama-server-version.txt` | `llama-server.exe --version` of the pinned build |
| `llama-list-devices-cuda.txt` | `llama-server.exe --list-devices`, native Windows. Under WDDM the sizes are the process budget beside another running model, not the GPU's 98,240 MiB |
| `llama-list-devices-container.txt` | `llama-server --list-devices` in the b11429 CUDA 13 container image (`server-cuda13-b11429`) under WSL, same GPU |
| `zip-entries.txt` | entry names and sizes of both release zips, with the SHA-256 and size of each zip; they match GitHub's asset digests for release b11429 |
| `ggml-cuda-targets.txt` | the GPU targets embedded in the pinned `ggml-cuda.dll` (SASS only, no PTX) |
| `context-overflow-400.json` | llama-server's answer to a prompt longer than its context (8192) |
| `cuda-archs-bad.txt` | the `system_info` line of the CUDA 12.8 image on a DGX Spark GB10, which answers `????` (`docs/DGX-SPARK-GB10-LLAMA-CPP-NOTES.md`) |
| `cuda-archs-gb10-sm121a.txt` | the `system_info` line of a working `121a` build on the same GB10 (same notes) |
| `generation-good.json` | **synthesized** OpenAI-style answer carrying the content the Spark returned to "What is 2+2?" (`4`) |
| `generation-bad.json` | **synthesized** answer carrying the GB10 symptom (`????????`) |

Not captured yet: the `CUDA : ARCHS =` line that this build writes to its own
log on the Spark. From `ggml-cuda-targets.txt` (`sm_121a`) and the GB10 notes
(`121a` reports `1210`), it is expected to list `1210`; the generation check
refuses the build when the line is missing.
