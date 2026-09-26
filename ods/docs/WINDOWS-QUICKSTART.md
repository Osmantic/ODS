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

1. **Capacity.** Setup needs 40 GB free on the Windows drive that stores Ubuntu and Docker data, and hardware virtualization (Intel VT-x or AMD SVM) turned on in the BIOS/UEFI. It stops before any change if either is missing.
2. **WSL and Docker Desktop.** If WSL is not ready, setup enables Windows Subsystem for Linux and Virtual Machine Platform with administrator approval. If Docker Desktop is missing, it installs it with winget (`--accept-license --backend=wsl-2`, which accepts the Docker Subscription Service Agreement). Both share one Windows restart. Setup registers a one-time `RunOnce` entry for your Windows user, so after you restart and sign in, a PowerShell window continues setup with the same options. Windows removes the entry before running it. The continuation script is `%LOCALAPPDATA%\ODS\portal-setup-resume.ps1`. Without winget, setup links the Docker Desktop installer and stops.
3. **Ubuntu.** Setup reuses a single existing distribution named Ubuntu, Ubuntu-24.04 or Ubuntu-26.04, and checks inside it that the release really is Ubuntu 24.04/26.04 (Pixel's requirement). Older releases such as Ubuntu-22.04 are never changed or selected automatically; if your only `Ubuntu` is older, rerun with `-Distro Ubuntu-24.04` to add a separate 24.04. If several qualifying distributions exist, select one with `-Distro <name>`. If none exists, setup downloads Ubuntu-24.04 under your Windows account and asks in PowerShell for a new Linux username and password. It creates that user with sudo rights, makes it the default and enables systemd in `/etc/wsl.conf`. The password is passed only on stdin to `chpasswd`. An existing Ubuntu that still opens as root gets its own interactive setup window instead.
4. **Checks.** Setup requires WSL 0.67.6 or newer, WSL2, a non-root default user and systemd, and stops with instructions otherwise. If an existing Ubuntu has systemd off, setup asks to turn it on (`[boot] systemd=true` in `/etc/wsl.conf`, other settings kept) and restarts that distribution.
5. **Docker connection.** Setup starts Docker Desktop if needed and waits up to 10 minutes for its engine. If `docker info` fails inside Ubuntu, it asks to turn on Docker's WSL integration for that distribution: it stops Docker Desktop, shuts WSL down (Docker's data disk must be released before it starts again), adds the distribution to `IntegratedWslDistros` in Docker's settings file, and starts Docker again. A Hyper-V Docker engine is reported, not changed. `docker compose version` must also work.
   With an NVIDIA GPU, update the Windows driver to 570 or newer first. Setup checks that Ubuntu sees the GPU and that Docker Desktop exposes its NVIDIA runtime, and stops before any Linux changes if not. Never install NVIDIA drivers or the container toolkit inside Ubuntu; see the [WSL2 GPU guide](WINDOWS-WSL2-GPU-GUIDE.md).
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

Pixel is the agent, not the model server. This change does not add a Windows GPU bridge. NVIDIA needs a supported driver and GPU access in WSL/Docker. AMD/Lemonade on Windows does not imply ROCm support in WSL. Use a supported detected backend, CPU, or an explicitly configured reachable endpoint; external endpoints are not automatically managed. See [WSL2 GPU guide](WINDOWS-WSL2-GPU-GUIDE.md).

## Existing native Windows installations

Setup detects native runtimes at `ODS_HOME` or `%USERPROFILE%\ods` and stops to avoid competing stacks. Check any older custom location yourself. No data migration or deletion is automatic. Preserve needed data and migrate/remove the old deployment before switching; removal is destructive and your explicit choice.

Manage existing native installations using their own `ods.ps1`. The native implementation remains at `ods/installers/windows/install-windows.ps1` for maintenance, not the recommended new-install path. Native commands do not manage the WSL runtime.

## Uninstall WSL ODS

Inside Ubuntu, use your chosen runtime directory:

```bash
cd ~/ods
./ods-uninstall.sh --force
```

Do not unregister Ubuntu to remove only ODS.

References: [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands), [Docker WSL integration](https://docs.docker.com/desktop/features/wsl/).
