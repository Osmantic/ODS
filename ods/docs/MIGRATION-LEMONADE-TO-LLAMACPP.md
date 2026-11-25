# AMD GPUs now run on llama.cpp

ODS used to serve models on AMD GPUs through Lemonade Server. It now runs
upstream [llama.cpp](https://github.com/ggml-org/llama.cpp)'s `llama-server`
on every platform, so the same engine, model files and API serve NVIDIA, AMD,
Apple and CPU installs:

- **Linux with an AMD GPU:** the official llama.cpp Vulkan container image, by
  default. A ROCm image is available as an option (see
  [Choosing Vulkan or ROCm](#choosing-vulkan-or-rocm-on-linux)).
- **Windows with an AMD GPU (the Portal):** the official `llama-server.exe`
  Vulkan build, which runs on Windows. The rest of the stack stays in WSL.
- **Windows with an AMD GPU (the native installer):** the same
  `llama-server.exe` build.
- **An OpenAI-compatible server you run yourself** (including your own
  Lemonade Server): ODS connects to it as an external model server.

ODS no longer uses Lemonade Server. It never uninstalls or reconfigures a
Lemonade Server installed on your computer. On Linux it removes the Docker
image and volumes of the Lemonade container it used to run only when you
uninstall ODS. If ODS installed Lemonade Server for you and you don't use it
yourself, you can remove it (see
[Removing what ODS left behind](#removing-what-ods-left-behind)).

## What an upgrade does

On Linux and in the Windows Portal, the upgrade keeps the model you selected
and its context size, as described below. Your model files stay where they are
and are not downloaded again.

### Linux with an AMD GPU

Rerun the installer of the new release, as for any update (see
[How do I update ODS?](../FAQ.md#how-do-i-update-ods)). `ods update` only
refreshes container images, and the source updater (`ods-update.sh update`)
refuses a stack that builds its own image, as the Lemonade-era AMD stack did.

The installer:

1. Moves the Lemonade-era settings in `.env` to llama.cpp before anything else
   reads them, and prints "Moved the AMD settings from Lemonade to the
   llama.cpp runtime; your model files and active model are kept."
   `ODS_MODE=lemonade` becomes `local`, `LLM_BACKEND` becomes `llama-server`,
   `LLM_API_BASE_PATH` becomes `/v1`, the `AMD_INFERENCE_*` settings describe
   the llama.cpp container, and the [retired settings](#retired-settings-and-options)
   are removed. The install log names the keys it changed, never their values.
2. Keeps `GGUF_FILE`, the active model and its context. llama.cpp serves that
   GGUF file from `data/models`. If Lemonade's model id named a different
   file, the installer says so.
3. Sets `AMD_INFERENCE_BACKEND=vulkan`, whatever backend Lemonade used. Instinct
   cards get ROCm instead, because they have no Vulkan driver.
4. Removes values the Lemonade-era installer wrote: `HSA_OVERRIDE_GFX_VERSION=11.5.1`
   and `ROCBLAS_USE_HIPBLASLT=1` for Lemonade's Strix Halo build, and a
   `LLAMA_SERVER_IMAGE` meant for another backend. `LLAMA_ARG_SPLIT_MODE=row`
   becomes `layer`, because Vulkan has no row split.
5. Downloads the pinned llama.cpp image and starts the stack. The
   `llama-server` service keeps its ports: `127.0.0.1:11434` on the host
   (`OLLAMA_PORT`) and `llama-server:8080` inside the ODS network.
6. Deletes the files ODS shipped for Lemonade when you never edited them, and
   names the edited ones it kept.

If the model router held a Lemonade route, the host agent replaces it and
proves the model on llama.cpp before the router serves it again.

The upgrade leaves the Lemonade container's Docker volumes and its
`ods-lemonade-server:latest` image in place and prints the command that
removes them (see [Removing what ODS left behind](#removing-what-ods-left-behind)).

On a computer without an AMD GPU, the upgrade only removes or clears the
retired settings.

### Windows (Portal)

Run the newer `install.ps1` over your existing installation, with the same
Ubuntu distribution and runtime directory (see the
[Windows Quickstart](WINDOWS-QUICKSTART.md#start-in-windows-powershell)).
Setup:

1. Downloads the pinned `llama-server.exe` Vulkan build into
   `%LOCALAPPDATA%\ODS\llama.cpp\`. It checks the download's size and SHA-256
   before extracting it.
2. Checks that it starts and that it sees your AMD GPU (`--version`,
   `--list-devices`). While this runs, Lemonade keeps serving and nothing else
   changes.
3. Stops only the Lemonade task ODS created, starts the new task
   `ODSLlamaServerRuntime-<your SID>` on the same port, and waits until it
   serves your model. When the task's plan names a model that is still in the
   model store, setup keeps that model and its context; otherwise it uses the
   recommended model.
4. If the new runtime does not come up, setup restores the previous files,
   restarts the Lemonade task if it was enabled, and stops with the reason.
   Your Lemonade setup is back as it was.
5. Once the model is proven, setup removes the old ODS Lemonade task and
   launcher files and shows a one-time notice. Lemonade Server itself, its
   cache and its settings are not touched.
6. Runs the Linux installer in Ubuntu, which moves its settings from Lemonade
   to the native llama-server route and prints "Moved the Windows GPU route
   from Lemonade to the native llama-server settings."

The new runtime listens on `127.0.0.1` only and requires an API key, which
ODS generates and keeps in a file only your Windows account can read. The
key never appears on a command line: setup passes it to the Linux installer
in an environment variable, and Ubuntu keeps it in `.env` as
`LLAMA_SERVER_API_KEY`.

The runtime's plan (`portal-runtime`) and the model store (`models`) stay in
`%LOCALAPPDATA%\ODS\lemonade`, which keeps its name.

### Windows (native installer)

Rerun `ods\installers\windows\install-windows.ps1` from the new source. The
installer:

1. Downloads and checks `llama-server.exe` as the Portal does, before it
   changes `.env` or any runtime. If that fails, nothing was changed.
2. Writes `.env` for llama-server and selects the model for your GPU, as on
   every rerun. A model file already in `data\models` that matches its
   checksum is not downloaded again.
3. Disables ODS's `ODSLemonadeRuntime` task and stops only the processes it
   started. Lemonade Server itself is not changed. A task with that name that
   ODS did not write is left alone, and the installer says why.
4. Copies the verified runtime to `<install>\llama-server`, keeps its API key
   file, launch options and log in `%LOCALAPPDATA%\ODS\native-runtime`, and
   starts the model through the new `ODSNativeLlamaRuntime` task, which runs
   at sign-in.
5. Once llama-server has proven its model, unregisters `ODSLemonadeRuntime`,
   deletes the `logs\lemonade-launch.task.ps1` launcher ODS wrote, and shows a
   one-time notice.

### If you connected ODS to your own Lemonade

Linux installs made with `--use-existing-lemonade` are moved to the generic
external model server option by an installer rerun or, in a git checkout,
by `ods-update.sh update`. Your server keeps running as before; ODS does not
start or stop it.

- ODS keeps the server's address as `EXTERNAL_LLM_URL`, without a trailing
  `/api/v1`, `/v1` or `/api`, and LiteLLM calls its `/v1` endpoint.
- It keeps the model from `LEMONADE_MODEL` as `EXTERNAL_LLM_MODEL`. Without a
  saved model, the upgrade changes nothing and stops with the installer
  options to use instead.
- An API key you gave ODS for the server moves to
  `config/litellm/external-upstream.key`, readable only by you. If that file
  already holds a different key, ODS keeps the file and says so.
- The installer shows "ODS no longer manages Lemonade. Your Lemonade server is
  now used as a generic OpenAI-compatible endpoint (EXTERNAL_LLM_URL)."

To change the model, make it available in your server, then rerun the
installer with `--external-llm-model <id>`. The Dashboard's **Models** page
shows "Model changes managed externally"; **Adopt loaded model** is gone. See
[Can ODS reuse a model already running in Ollama or LM Studio?](FAQ.md#can-ods-reuse-a-model-already-running-in-ollama-or-lm-studio)
for the external server options.

### AMD GAIA

The AMD GAIA library recipe is removed too, because GAIA's local models need
Lemonade Server. An installed GAIA keeps running until you disable it. The
[changelog](../CHANGELOG.md) explains how to remove it.

## Retired settings and options

These `.env` settings are no longer used. They still validate for one
release, so an older `.env` does not stop an update. Upgrades remove them, and
the Dashboard's Settings page lists one only while your `.env` has it and
lets you clear it:

`LEMONADE_EXTERNAL`, `LEMONADE_HOST_TRANSPORT`, `LEMONADE_BASE_URL`,
`LEMONADE_CONTAINER_BASE_URL`, `LEMONADE_API_BASE_PATH`, `LEMONADE_MODEL`,
`LEMONADE_API_KEY`, `LITELLM_LEMONADE_API_KEY`, `LEMONADE_SERVER_IMAGE`,
`LEMONADE_LLAMACPP`, `LEMONADE_LLAMACPP_ROCM_BIN`, `LLAMA_CPP_REF`,
`AMDGPU_TARGET`, `HSA_XNACK`.

These values are retired the same way, and upgrades rewrite them:
`ODS_MODE=lemonade` (read as `local`), `LLM_BACKEND=lemonade`,
`AMD_INFERENCE_RUNTIME=lemonade`, `AMD_INFERENCE_BACKEND` `auto`, `cpu` or
`npu`, and `AMD_INFERENCE_RUNTIME_MODE` `external-lemonade` or
`windows-legacy-lemonade`.

Installer options:

| Old option | Use instead |
|---|---|
| `--use-existing-lemonade [--lemonade-url U] [--lemonade-model M]` | `--external-llm-url U --external-llm-provider openai-compatible [--external-llm-model M]` |
| `--lemonade-api-key K` (with `--use-existing-lemonade`) | `--external-llm-key-file PATH`, a file only you can read that holds the key |
| `--lemonade-url`, `--lemonade-model`, `--lemonade-host-transport`, `--lemonade-context-size`, `--lemonade-gpu-name`, `--lemonade-gpu-vram-mb` (sent by older Windows setup) | `--native-llm-url`, `--native-llm-model`, `--native-llm-host-transport`, `--native-llm-context-size`, `--native-llm-gpu-name`, `--native-llm-gpu-vram-mb` |
| `ODS_MODE=lemonade` in the installer's environment | Nothing: AMD installs use `ODS_MODE=local` |

The old options still work for one release and print the replacement. Without
`--lemonade-url`, `--use-existing-lemonade` uses `http://localhost:13305`.

The Dashboard API's `/api/models/external-observation` and
`/api/models/external-adopt`, and the host agent's
`/v1/model/external-observation`, `/v1/model/external-adopt` and
`/v1/runtime/lemonade/ensure`, answer HTTP 410 (`external_lemonade_removed`)
for one release.

## Choosing Vulkan or ROCm on Linux

Vulkan is the default. The image brings its own Vulkan driver (Mesa RADV), so
the host needs only the `amdgpu` kernel driver's `/dev/dri` render node and
the `video` and `render` groups. It covers RDNA cards and APUs, Strix Halo
included.

The ROCm image is opt-in, except on Instinct cards: they have no Vulkan
driver, so the installer selects ROCm for them. The ROCm image is about 7 GB,
and it also needs the ROCm compute device `/dev/kfd`. To switch, rerun the
installer with the backend set, for example from your ODS source folder:

```bash
AMD_INFERENCE_BACKEND=rocm ./install.sh
```

The installer keeps the choice in `.env`, downloads the ROCm image and adds
`docker-compose.amd-rocm.yml` to the stack. To go back, rerun it with
`AMD_INFERENCE_BACKEND=vulkan`.

The ROCm image is built for gfx908, gfx90a, gfx942, gfx1030, gfx1100, gfx1101,
gfx1102, gfx1150, gfx1151, gfx1200 and gfx1201. For gfx1031 to gfx1036 the
installer sets `HSA_OVERRIDE_GFX_VERSION=10.3.0`, and for gfx1103 `11.0.0`.
For any other GPU it sets no override and warns that the model may not load;
set `AMD_INFERENCE_BACKEND=vulkan` if it does not.

## Removing what ODS left behind

**Windows (Portal).** If setup's notice says ODS installed Lemonade Server for
you and you don't use it yourself, uninstall it from **Settings > Apps**. ODS's
own Lemonade task and launchers are already gone. A backup of the previous
runtime plan stays in `%LOCALAPPDATA%\ODS\lemonade\`, in a folder named
`portal-runtime.lemonade-backup-<time>-<id>`, until you delete it. Lemonade-era
logs there are kept for diagnosis; you can delete them too. Do not delete the
`lemonade` folder itself, or its `portal-runtime` and `models` folders: the
llama.cpp runtime uses them.

If setup said it left a former task registered because it changed or restarted
after ODS stopped it, remove it from normal PowerShell once you no longer need
it:

```powershell
$odsSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Unregister-ScheduledTask -TaskPath '\' -TaskName "ODSLemonadeRuntime-$odsSid" -Confirm:$false
```

**Windows (native installer).** Uninstall Lemonade Server from
**Settings > Apps** as above if the notice says ODS installed it. The
installer already removed the `ODSLemonadeRuntime` task. If it reported that
it left the task registered (disabled), remove it from normal PowerShell once
you no longer need it:

```powershell
Unregister-ScheduledTask -TaskPath '\' -TaskName 'ODSLemonadeRuntime' -Confirm:$false
```

**Linux.** The volumes of the former Lemonade container and its image are no
longer used. Your model files are in `data/models`, not in these volumes.
`ods-uninstall.sh` removes them (the volumes unless you pass `--keep-data`).
To free the space now, run what the upgrade printed. With the default Compose
project name `ods`:

```bash
docker volume rm ods_lemonade-cache ods_lemonade-llama ods_lemonade-recipe
docker image rm ods-lemonade-server:latest
```

If you set `COMPOSE_PROJECT_NAME`, the volume names start with that name
instead. Files the installer kept because you edited them are named in its
output; nothing uses them now, so delete them when you no longer need them.

## If an upgrade stops

- **Windows (Portal).** A failure before the new runtime has proven its model
  restores the previous runtime, as described above, and setup ends with "The
  llama.cpp runtime did not start" and the reason. If restoring also fails,
  setup says so: ODS changed only its own files under
  `%LOCALAPPDATA%\ODS\lemonade`, and its backup is kept there. Rerun setup.
- **Windows (native installer).** A failure while preparing llama.cpp changes
  nothing. A failure after that leaves the former `ODSLemonadeRuntime` task
  registered but disabled; Lemonade is not restarted. Fix the cause the
  installer names and rerun it.
- **Linux.** The `.env` change is all or nothing: if it fails, the installer
  stops before any other step and nothing was changed. If a later step fails,
  rerun the installer. The settings move changes nothing on a `.env` that has
  already moved.

This release has no way back to a Lemonade runtime that ODS runs. To keep
using Lemonade, run it yourself and connect ODS to it as an
[external server](#if-you-connected-ods-to-your-own-lemonade).

## Troubleshooting

- **`llama-server.exe` exits at once with a missing DLL (0xC0000135).**
  Install the Microsoft Visual C++ 2015-2022 Redistributable (x64), for
  example with `winget install Microsoft.VCRedist.2015+.x64`, then rerun
  setup.
- **Smart App Control or an application control policy blocks it.** The
  llama.cpp binaries are not signed; ODS pins them by SHA-256 instead. Setup
  names the policy it found. ODS never changes these settings.
- **No usable Vulkan device is found (Windows).** Update the AMD Adrenalin
  driver from amd.com/support and restart Windows, then rerun setup. ODS does
  not fall back to the CPU silently: a new install continues on the CPU and
  says so, and an existing install stops without changing anything.
- **`/dev/dri` or its render node is missing (Linux).** The installer warns
  about it. The `amdgpu` driver may not be loaded: try `sudo modprobe amdgpu`
  or reboot. The ROCm image also needs `/dev/kfd` (`sudo modprobe amdkfd`).
- **Another program holds the port (Windows).** Setup names it. Set
  `AMD_INFERENCE_PORT` to a free port in the same PowerShell window, for
  example `$env:AMD_INFERENCE_PORT = "18080"`, and rerun setup.
- **A path or model name with non-ASCII characters (Windows).** llama.cpp on
  Windows cannot open such a path unless the drive has 8.3 short names; setup
  says so. Use an ASCII user or folder name, and rename a model file whose
  name is not ASCII.
- **Setup warns that the model needs more memory than Vulkan reports
  (Windows).** llama.cpp keeps the rest in system memory, which is slower; a
  larger UMA frame buffer in the BIOS or a smaller model avoids that.
- **Chat stopped working after an interrupted update.** Rerun the same
  installer. It recognizes the migrated runtime and finishes the remaining
  steps; on Linux and in the Portal it keeps your model.
