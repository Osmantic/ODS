# llama.cpp b9014 `win-cpu-x64` / `win-cpu-arm64` fixtures

`zip-entries.txt` lists the entry names and sizes of the two official
ggml-org/llama.cpp b9014 CPU release zips, with the SHA-256 and size of each
zip. Measured on 2026-10-31 by downloading both assets from
`https://github.com/ggml-org/llama.cpp/releases/download/b9014/` (the same
download also reproduced the already-pinned `win-vulkan-x64` digest,
`6cd4bc7a...3b0f`, 33541404 bytes).

Used by `tests/contracts/test_windows_native_cpu_runtime_pins.py` (every
`required_files` entry of `config/backends/cpu.json` must be shipped by its
zip) and `tests/contracts/test-windows-native-llama-runtime.ps1`.
