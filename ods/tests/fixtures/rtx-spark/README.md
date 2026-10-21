# RTX Spark probe fixtures

Read-only output captured on an NVIDIA RTX Spark (Windows 11 on Arm, NVIDIA N1X,
compute capability 12.1, WDDM, driver 616.62, firmware split of 98,240 MiB for the
GPU) on 2026-10-17. Device UUIDs, user names, paths, host names, account ids and
anything about other software on that machine were removed. Nothing here
identifies the machine or its owner; `tests/contracts/test-windows-spark-detection.ps1`
checks every file in this folder for UUIDs, Windows account ids and user paths.

| File | What it is |
|---|---|
| `nvidia-smi-windows-probe.txt` | Windows `nvidia-smi --query-gpu=name,pci.device_id,compute_cap,memory.total,driver_model.current --format=csv`, as captured: the N1X and the NVIDIA NPU rows. |
| `nvidia-smi-windows.csv` | The rows of the query Windows setup runs (the five fields above plus `driver_version`, `--format=csv,noheader`). Composed: the first five fields are the capture above (the test checks this); the N1X `driver_version` comes from the same machine's WSL query below. The NPU's `driver_version` was not captured, so it is `[N/A]`. |
| `nvidia-smi-wsl.txt` | `nvidia-smi --query-gpu=name,pci.device_id,compute_cap,memory.total,driver_version --format=csv` inside Ubuntu on WSL, as captured. WSL lists only the N1X, not the NPU. |
| `windows-processor.json` | `Win32_Processor` on that machine: `Architecture` 12 is ARM64, firmware virtualization on. |
| `memory-raised-96.json` | The memory layout the RTX Spark memory planner reads (`Get-ODSSparkMemoryLayout`, same shape) for that machine as measured: 98,240 MiB carve-out, 30.49 GiB visible, commit limit 126.49 GiB, a fixed 98,304 MB page file, no `.wslconfig`, 25.05 GiB free on C:. The idle commit (12 GiB) comes from the same machine; the WSL commit and running model are fixture values. |
| `memory-factory-64.json` | **Synthesized**: the factory split as documented (63,936 MiB carve-out, about 62.5 GiB visible), no page file, a fresh PC. |
| `memory-factory-64-pf64.json` | **Synthesized**: the factory split with a fixed 64 GB page file and ODS's 16 GB WSL limit. |
| `memory-96-nopf.json` | **Synthesized**: the raised 96 GB split with no page file. |
| `memory-pending.json` | **Synthesized**: the factory split right after a 32 GB page file was set, waiting for the restart. |

The factory firmware split (63,936 MiB for the GPU) was not measured on this
machine. Tests that use it substitute that figure into these rows and say so.
Each memory fixture says in its `_source` field whether it was measured or
synthesized; `tests/contracts/test-windows-spark-memory.ps1` runs the planner on
all of them.
