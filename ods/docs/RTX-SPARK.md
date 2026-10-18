# ODS on an NVIDIA RTX Spark (Windows on Arm)

This page explains what the Windows installer (`install.ps1`) does on a PC with
an NVIDIA RTX Spark (the N1X GPU) running Windows 11 on Arm, what it may ask
you, and what it leaves on Windows. Everything else about the Windows install
is in [WINDOWS-QUICKSTART.md](WINDOWS-QUICKSTART.md).

Need help? Ask on the ODS Discord: https://discord.gg/4ntNp9MAwC

## What is different on an RTX Spark

On most Windows PCs with an NVIDIA GPU, the AI model runs in a container
inside Ubuntu (WSL). On an RTX Spark it runs directly on Windows instead, with
the llama.cpp build for NVIDIA GPUs on Windows on Arm:

- **llama.cpp `b11429`, `win-cuda-13.4-arm64`**, from the ggml-org GitHub
  release. Setup downloads its two archives (about 285 MiB) and checks each
  against the SHA-256 that ODS pins. It is installed under
  `%LOCALAPPDATA%\ODS\llama.cpp\b11429-win-cuda-13.4-arm64`.
- The model server starts at sign-in as your own scheduled task
  (`ODSLlamaServerRuntime-<your account id>`). It listens on `127.0.0.1` only
  and needs an API key, which ODS keeps in a file only your account can read.
- Ubuntu, Docker and the ODS services stay off the GPU. LiteLLM, the model
  router and Portal reach the model server on Windows.

Setup recognizes the PC by what Windows reports: Windows runs on Arm, and
`nvidia-smi` lists exactly one NVIDIA CUDA GPU. The NVIDIA NPU that Windows
also lists is not a GPU and is never used by ODS.

## What setup does, in order

1. **Checks what ODS already runs.** If a model server that this ODS release
   does not manage is registered under ODS's task name and is running, setup
   stops before changing anything (see
   [Another model server under ODS's task name](#another-model-server-under-odss-task-name)).
2. **Checks free disk space and virtualization**, as on every Windows PC.
3. **Plans the model from your PC's memory** (see below) and checks that the
   files fit on the drive. Nothing has been downloaded yet.
4. **Asks to download llama.cpp and a 1.3 GB test model** (Qwen 3.5 2B), then
   checks llama.cpp on your GPU: it must find the CUDA GPU, report CUDA
   kernels for compute capability 12.1, and answer a short test question
   ("What is 2+2?") correctly. The result is saved and reused until the
   llama.cpp build, the NVIDIA driver or the GPU changes.
5. **Offers memory changes** that let a larger model fit, when one would (see
   below). Each one asks first.
6. **Prepares WSL, Ubuntu and Docker Desktop** as on every Windows PC. If a
   Windows restart is needed, there is only one, and setup continues by itself
   after you sign in again.
7. **Downloads the model** (every file of it), starts it with llama.cpp on
   Windows, checks that it serves the right model, then installs ODS in Ubuntu.

The NVIDIA driver must be version 616.41 or newer (the CUDA 13.4 minimum).
Update it with the NVIDIA App or from nvidia.com. Never install NVIDIA drivers
inside Ubuntu.

## The model ladder

Setup only picks models that have been run and checked with this llama.cpp
build on this kind of PC. It tries them best first and takes the first one
that fits the GPU's memory, what Windows can lend it, and the free disk space:

| Model | Context | GPU memory | Windows commit | Download |
|---|---|---|---|---|
| Qwen 3.6 35B-A3B | 128K tokens | about 23.8 GiB | about 25.8 GiB | 22.1 GB |
| Qwen 3.5 9B (fallback) | 64K tokens | about 7.8 GiB | about 9.3 GiB | 5.7 GB |
| Qwen 3.5 4B (fallback) | 64K tokens | about 5.0 GiB | about 6.6 GiB | 2.7 GB |
| Qwen 3.5 2B (fallback, the test model) | 64K tokens | about 2.3 GiB | about 2.9 GiB | 1.3 GB |

When no model fits, the model runs on the CPU inside Ubuntu instead (much
slower), and setup says why. Larger models join the list as they are proven on
this build. You can still choose other models on the Models page.

## The firmware memory split

The N1X has no memory of its own. The PC's firmware gives part of the RAM to
the GPU and the rest to Windows. You can see the GPU's share in PowerShell:

```powershell
nvidia-smi --query-gpu=name,memory.total --format=csv
```

A larger GPU share fits larger models but leaves less memory for Windows and
your programs. The model plan follows whatever split your PC has; the
largest models planned for this PC need a GPU share of about 80 GiB or more.
ODS never changes the split. Your PC maker's documentation says whether and
how it can be changed.

## Windows memory, the page file and WSL

Windows counts every GPU allocation against its **commit limit** (the memory
Windows can lend programs: visible RAM plus the page file), even inside the
GPU's share. So a model can fit the GPU and still not start when the commit
limit is too small. Setup sizes the plan with both. Two changes can make room;
setup offers them only when a better model would then fit:

- **A WSL memory limit.** Ubuntu and Docker (WSL) may use up to half of
  Windows' memory, and Windows counts that against the same limit. Setup can
  add `memory=16GB` under `[wsl2]` in `%USERPROFILE%\.wslconfig`, marked as set
  by ODS. It never changes a `memory=` line you wrote, keeps a copy of your old
  file in `%LOCALAPPDATA%\ODS`, and writes
  `%LOCALAPPDATA%\ODS\restore-windows-settings.ps1`, which puts it back. The new
  limit applies when WSL restarts; setup asks before it restarts WSL (that
  stops Ubuntu, the ODS services and Docker Desktop's containers). If you say
  no, WSL keeps its old limit, and setup plans the model with that one until
  Windows restarts or a later setup run restarts WSL. That later run asks only
  about the restart.
- **A larger page file** on the system drive: a fixed size, in steps of
  16 GB, never smaller than the one you have. Windows asks for administrator
  permission once, and the change applies after a restart. Use **Restart**,
  not **Shut down**: with Fast Startup, Shut down may not apply it.

If you say no, setup installs the largest proven model that fits now and
tells you how to get the larger one later: rerun the same `install.ps1`
command and answer yes.

Every time the model starts (at sign-in too), the model server first checks
that Windows can lend it the memory it needs and that the GPU has room. Right
after sign-in it waits up to two minutes for other programs to settle. If
memory is still short, it does not start and the Models page shows why. Close
large programs, then press **Start anyway** there. Start anyway can make other
programs fail when memory really runs out.

## Questions setup may ask

| Question | When | If you say no |
|---|---|---|
| Download llama.cpp for NVIDIA GPUs on Windows on Arm, and the test model? | First install | The model runs on the CPU inside Ubuntu (slower). |
| Install the Microsoft Visual C++ Redistributable for Arm64? | llama.cpp cannot start because it is missing (0xC0000135) | The model runs on the CPU. You can also install it from https://aka.ms/vs/17/release/vc_redist.arm64.exe and rerun setup. |
| Limit WSL to 16 GB? Restart WSL now? | A WSL limit lets a better model fit | Nothing changes; the plan uses the memory Windows has now. A no to the restart only: the limit waits for WSL to restart, and the plan uses the limit WSL has now. |
| Set a larger page file? | A page file lets a better model fit | Nothing changes; setup installs the model that fits now. |
| Continue with the model on the CPU? | The free disk is too small for any GPU model | Setup stops; nothing was downloaded or changed. |

With `-NonInteractive`, setup asks nothing and changes no memory setting. On a
first install it keeps the model on the CPU, or stops when the disk is too
small for any GPU model.

## When llama.cpp cannot run on this PC

On a first install, setup says why and continues with the model on the CPU
inside Ubuntu. Common causes are a missing Visual C++ runtime, a missing CUDA
GPU (update the NVIDIA driver and restart Windows), a build without kernels for
this GPU, or Smart App Control or an application control policy blocking the
unsigned llama.cpp files (ODS does not change those settings). Fix the cause
and rerun setup to use the GPU.

When ODS's llama.cpp already serves your model (a rerun), setup stops instead
and your current model is not changed.

## Another model server under ODS's task name

If a model server that this ODS release does not manage runs under ODS's task
name, setup stops before changing anything and prints its task name. To let
ODS replace it, stop and disable it in normal PowerShell (not as
administrator), then rerun setup:

```powershell
Stop-ScheduledTask -TaskPath '\' -TaskName '<task name>'
Disable-ScheduledTask -TaskPath '\' -TaskName '<task name>'
```

Setup then saves that task's definition (an `.xml` file) and a copy of its
runtime folder under `%LOCALAPPDATA%\ODS\replaced-tasks` before it replaces
them, and prints where. If the replacement fails, setup puts the old task back
(disabled) with its files. To bring it back later, remove ODS's task, copy the
saved folder back to `%LOCALAPPDATA%\ODS\lemonade\portal-runtime`, and register
the saved definition:

```powershell
Register-ScheduledTask -TaskPath '\' -TaskName '<task name>' -Xml (Get-Content -Raw '<saved .xml file>')
```

## An older install that ran the model in a container

An RTX Spark installed with an earlier ODS release ran the model in a
container in Ubuntu (the oldest releases with a CUDA 12 build that cannot use
this GPU correctly). Rerunning setup stops ODS's own model container (only
that one), so it no longer holds the GPU's memory, starts the model with
llama.cpp on Windows, and then updates Ubuntu. Model files in Ubuntu stay until
you delete them on the Models page. If the switch fails, setup starts the old
container again.

## The Models page

You can switch models on the Models page as on other PCs. A model split into
several files needs every part in the Windows model store; a missing part is
named and nothing is stopped. A switch that Windows cannot lend enough memory
for is refused before anything changes, and your current model keeps running.
Hugging Face imports are checked against the llama.cpp build that actually
runs here (`b11429`).

## Sleep and hibernate

ODS does not stop the model when the PC sleeps. How sleep, hibernate and
Modern Standby behave with a model loaded on an RTX Spark has not been
measured yet; this section will say what is known once it has.

## What stays on Windows after uninstall

Uninstalling ODS (in Ubuntu) turns off its Windows sign-in startup but keeps
these, and lists the ones that apply when it finishes:

| What | Where |
|---|---|
| The model files | `%LOCALAPPDATA%\ODS\lemonade\models` |
| llama.cpp | `%LOCALAPPDATA%\ODS\llama.cpp` |
| The page file ODS set | System Properties > Advanced > Performance > Settings > Advanced > Virtual memory |
| ODS's WSL memory limit | Undo it with `& "$env:LOCALAPPDATA\ODS\restore-windows-settings.ps1"` in normal PowerShell |

Delete the folders to free their space. The restore script asks before it
changes anything and never asks for administrator rights; for the page file it
prints the size you had before and the steps to set it.
