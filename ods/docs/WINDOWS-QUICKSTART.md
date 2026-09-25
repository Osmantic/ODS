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

1. If WSL is not ready, setup offers Windows feature preparation with administrator approval. It then stops: restart if requested and rerun the same command. No automatic reboot or resume task is created.
2. If Ubuntu is absent, setup offers to download Ubuntu-24.04 under your Windows account. Complete Linux user/password creation in the Ubuntu window, then type `exit` to return. The default Linux user must not be root.
3. Setup checks WSL2 and systemd. Missing prerequisites stop installation with instructions; existing distributions are not converted and `/etc/wsl.conf` is not overwritten automatically.
4. Start Docker Desktop, enable its WSL2 engine and **Settings > Resources > WSL Integration** for Ubuntu. Both `docker info` and `docker compose version` must work inside Ubuntu. Setup checks them and stops with instructions if needed; it does not install another Docker engine.
5. The Linux installer runs with `--pixel --no-hermes --no-openclaw`. Enter the Ubuntu sudo password when requested and complete model/service selections.
6. After installation, the wrapper verifies Pixel gateway/ingress services, private ingress health and the dashboard HTTP endpoint. Send a message in Portal to verify model inference too.

Fix reported prerequisites and rerun the same command. Windows/UAC/restart and Ubuntu first-run setup may require interaction. Never send passwords through chat.

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
