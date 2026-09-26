# ODS Windows Quickstart

## Start in Windows PowerShell

Use a normal, non-Administrator PowerShell window. The installer guides Ubuntu/WSL2 preparation and installs Pixel/Portal there. There is no native Windows or Hermes fallback.

```powershell
$ProgressPreference = "SilentlyContinue"
$odsSrc = Join-Path $env:TEMP ("ods-install-" + [guid]::NewGuid().ToString("N"))
$odsZip = Join-Path $odsSrc "ods-main.zip"
New-Item -ItemType Directory -Path $odsSrc | Out-Null
Invoke-WebRequest "https://github.com/Osmantic/ODS/archive/refs/heads/main.zip" -OutFile $odsZip
Expand-Archive -LiteralPath $odsZip -DestinationPath $odsSrc -Force
cd (Get-ChildItem -LiteralPath $odsSrc -Directory | Select-Object -First 1).FullName
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install.ps1
```

Pixel uses source bundled in public Osmantic/ODS, not a private repository.

## Setup stages

Every stage asks before changing anything. Answer `y` to continue.

1. **Capacity.** Setup needs 40 GB free on the Windows drive that stores Ubuntu and Docker data, and hardware virtualization (Intel VT-x or AMD SVM) turned on in the BIOS/UEFI. It stops before installing WSL if either is missing. When WSL is already installed (for example on a rerun), low space is only a warning.
2. **WSL and Docker Desktop.** If WSL is not ready, setup enables Windows Subsystem for Linux and Virtual Machine Platform with administrator approval. If Docker Desktop is missing, it installs it with winget (`--accept-license --backend=wsl-2`, which accepts the Docker Subscription Service Agreement). Both share one Windows restart. Setup registers a one-time `RunOnce` entry for your Windows user, so after you restart and sign in, a PowerShell window continues setup with the same options. Windows removes the entry before running it. The continuation script is `%LOCALAPPDATA%\ODS\portal-setup-resume.ps1`. Without winget, setup links the Docker Desktop installer and stops.
3. **Ubuntu.** Setup reuses a single existing distribution named Ubuntu, Ubuntu-24.04 or Ubuntu-26.04, and checks inside it that the release really is Ubuntu 24.04/26.04 (Pixel's requirement). Older releases such as Ubuntu-22.04 are never changed or selected automatically; if your only `Ubuntu` is older, rerun with `-Distro Ubuntu-24.04` to add a separate 24.04. If several qualifying distributions exist, select one with `-Distro <name>`. If none exists, setup downloads Ubuntu-24.04 under your Windows account and asks in PowerShell for a new Linux username and password. It creates that user with sudo rights, makes it the default and enables systemd in `/etc/wsl.conf`. The password is passed only on stdin to `chpasswd`. An existing Ubuntu that still opens as root gets its own interactive setup window instead.
4. **Checks.** Setup requires WSL 0.67.6 or newer, WSL2, a non-root default user and systemd, and stops with instructions otherwise. If an existing Ubuntu has systemd off, setup asks to turn it on (`[boot] systemd=true` in `/etc/wsl.conf`, other settings kept) and restarts that distribution.
5. **Docker connection.** Setup starts Docker Desktop if needed and waits up to 10 minutes for its engine. It then waits up to a minute for `docker info` to work inside Ubuntu, because Docker Desktop connects to a distribution a few seconds after it starts. If it still does not, setup shows the exact steps (Docker Desktop > Settings > Resources > WSL integration > turn on the distribution > **Apply & restart**), brings Docker Desktop to the front and continues by itself as soon as Docker answers inside Ubuntu. Setup never edits Docker's settings or stops Docker Desktop. `docker compose version` must also work.
   With an NVIDIA GPU, update the Windows driver to 570 or newer first. Setup checks that Ubuntu sees the GPU and that Docker Desktop exposes its NVIDIA runtime, and stops before any Linux changes if not. Never install NVIDIA drivers or the container toolkit inside Ubuntu; see the [WSL2 GPU guide](WINDOWS-WSL2-GPU-GUIDE.md).
   With an AMD GPU (and no NVIDIA driver), the model runs in Lemonade Server on Windows, because Docker Desktop passes only NVIDIA GPUs into WSL containers. Setup reads the GPU and its memory in Windows, picks the model the native Windows installer would (for example `qwen3.5-9b` with 64K context on a 16 GB card), asks to install the pinned Lemonade Server for your Windows user, downloads the model once to `%LOCALAPPDATA%\ODS\lemonade\models` (checksum verified), and runs Lemonade on `127.0.0.1` through the `ODSLemonadeRuntime` scheduled task, which starts at sign-in. An existing Lemonade Server install is reused, including 10.7+ releases (configured through Lemonade's local API). Lemonade gets port 8080, or the first free one of 13305, 8000, 18080, 28080 when another program holds it; set `AMD_INFERENCE_PORT` to choose one. It loads the model on the GPU before Ubuntu is touched, then passes `--lemonade-url`, `--lemonade-model` and the GPU tier to the Linux installer; containers reach Lemonade at `host.docker.internal`. An AMD GPU with under 4 GB, or declining Lemonade, keeps the CPU route.
6. **ODS.** The Linux installer runs with `--pixel --no-hermes --no-openclaw`. When Ubuntu asks for your `[sudo] password`, type the Ubuntu password; nothing appears while you type.
7. **Verification.** The wrapper verifies Pixel gateway/ingress services, private ingress health, the dashboard HTTP endpoint and the authenticated Portal availability API. A dashboard that opens while its agent is unavailable is a failed verification. On success it opens Portal and creates an **ODS Portal** desktop shortcut (not in `-NonInteractive` runs). Send a message in Portal to verify model generation too.

`-NonInteractive` never installs prerequisites or changes Docker Desktop settings; it only checks them. Never send passwords through chat.

## Options and location

Use `wsl -l -v` to find distribution names. For an existing Ubuntu:

```powershell
.\install.ps1 -Distro Ubuntu
```

The runtime normally lives at `~/ods` inside Ubuntu. The ZIP is the source checkout; keep it while using its WSL lifecycle helper. A custom runtime path must be an absolute Linux path:

```powershell
.\install.ps1 -InstallDir /home/youruser/ods
```

This does not move Ubuntu's virtual disk or Docker storage. Windows drive paths are rejected.

- `-DryRun`: show the plan without changing prerequisites or services.
- `-NonInteractive`: require prepared prerequisites; do not offer prerequisite installation.
- `-Tier 1..4`, `-Cloud`: forward model selection.
- `-Voice`, `-Workflows`, `-Rag`, `-Recommended`, `-NoRecommended`: service choices.
- `-All`, `-Comfyui`, `-NoComfyui`, `-Langfuse`, `-NoLangfuse`: optional services; explicit disables override `-All`.
- `-NoBootstrap`, `-Force`, `-Lan`: corresponding Linux options.
- `-SummaryJsonPath <Linux path>`: Linux summary output location.
- `-NoHermes`: accepted for compatibility; Hermes is always disabled. `-Hermes` and deprecated `-OpenClaw` are rejected, and `-All` cannot enable them.

## Already inside Ubuntu?

With the same systemd/Docker prerequisites:

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS
bash install.sh --pixel --no-hermes --no-openclaw
```

Do not run the PowerShell block in Bash.

## Verify Portal/Pixel

Open the printed dashboard URL, normally **http://localhost:3001**, check availability and send a message. **http://localhost:3000** is separate Open WebUI. Inside Ubuntu:

```bash
cd ~/ods
./ods status
sudo systemctl status openclaw-gateway.service pixel-ingress.service --no-pager
```

For failures, inspect the installer log and `sudo journalctl -u openclaw-gateway.service -u pixel-ingress.service -n 80 --no-pager`. If systemd is missing, enable `systemd=true` under `[boot]` in `/etc/wsl.conf`, preserving other settings, then run `wsl --terminate Ubuntu-24.04` in PowerShell and reopen Ubuntu.

## GPU placement

Pixel is the agent, not the model server. NVIDIA runs the model inside WSL (Docker Desktop's NVIDIA runtime). AMD runs it in Lemonade Server on Windows (see step 5); ROCm is not used in WSL. Without a usable GPU the model runs on the CPU. See [WSL2 GPU guide](WINDOWS-WSL2-GPU-GUIDE.md).

For AMD, Windows setup automatically passes `--lemonade-host-transport model-router` to the Linux installer and saves `LEMONADE_HOST_TRANSPORT=model-router` in the runtime `.env`. Windows and Ubuntu can have different localhost listeners. The WSL host agent therefore checks the Windows model through this installation's running model-router container, using its configured `host.docker.internal` endpoint. Before sending a request, it checks the container's ODS labels, installation mounts and Lemonade endpoint. Missing or mismatched ownership keeps the route unverified; model identity, context and a successful completion are still required for readiness.

Lemonade stays bound to Windows `127.0.0.1`; this transport does not enable LAN access or select cloud inference. The Linux setting `LEMONADE_EXTERNAL=true` means Lemonade is managed outside the Linux stack, on the same Windows computer. Other Lemonade installations use the default `--lemonade-host-transport direct`, which probes from the host agent's own network context.

The `ODSLemonadeRuntime` task starts at Windows sign-in, restores the selected model and context, and verifies the loaded model before setup proceeds. Its launcher and configuration live in `%LOCALAPPDATA%\ODS\lemonade\portal-runtime`, so removing the temporary installer checkout does not break the next startup. Startup failures are recorded in `lemonade-launch.log` in that directory.

After a Windows restart, sign in, let Docker Desktop connect to Ubuntu, then check Portal availability and send a message again. A registered task or a healthy Lemonade API alone does not prove that model generation resumed successfully.

## Existing native Windows installations

Setup detects native runtimes at `ODS_HOME` or `%USERPROFILE%\ods` and stops to avoid competing stacks. Check any older custom location yourself. No data migration or deletion is automatic. Preserve needed data and migrate/remove the old deployment before switching; removal is destructive and your explicit choice.

Manage existing native installations using their own `ods.ps1`. The native implementation remains at `ods/installers/windows/install-windows.ps1` for maintenance, not the recommended new-install path. Native commands do not manage the WSL runtime.

To remove a native installation completely before switching (containers, Docker volumes, data and models; this cannot be undone), run from its runtime folder:

```powershell
cd $env:USERPROFILE\ods
.\ods.ps1 uninstall --force
```

## Uninstall WSL ODS

Inside Ubuntu, use your chosen runtime directory:

```bash
cd ~/ods
./ods-uninstall.sh --force
```

Do not unregister Ubuntu to remove only ODS.

On AMD machines, also remove the Windows side from PowerShell: `Unregister-ScheduledTask -TaskName ODSLemonadeRuntime -Confirm:$false`, uninstall **Lemonade Server** in Settings > Apps, and delete `%LOCALAPPDATA%\ODS\lemonade` (the downloaded model).

References: [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands), [Docker WSL integration](https://docs.docker.com/desktop/features/wsl/).
