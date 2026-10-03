# ODS Windows Quickstart

> **Release channel:** the install commands on this page fetch development `main`, which is not signed. A signed-source path is staged in [Verified Install Preview](VERIFIED_INSTALL_PREVIEW.md); it is not active until the first eligible immutable release is published, and historical `v3.0.0` is not eligible.

## Start in Windows PowerShell

Use a normal, non-Administrator PowerShell window. The installer guides Ubuntu/WSL2 preparation and installs Pixel/Portal there. There is no native Windows or Hermes fallback.

Setup requires a non-elevated Windows user session. If UAC is disabled or you
use the built-in Administrator account and every PowerShell window is elevated,
use a standard Windows account or enable UAC and sign in again. Setup does not
support an elevated UAC-disabled session; it requests elevation separately for
Windows prerequisites.

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
   For WSL NAT with Docker Desktop, setup prepares the current private Ubuntu address for the authenticated ODS host agent. Full `ods start` refreshes automatically managed addresses after WSL restarts. Explicit `ODS_AGENT_BIND` or `ODS_AGENT_HOST` settings are preserved on setup reruns.
   With an NVIDIA GPU, update the Windows driver to 570 or newer first. Setup checks that Ubuntu sees the GPU and that Docker Desktop exposes its NVIDIA runtime, and stops before any Linux changes if not. Never install NVIDIA drivers or the container toolkit inside Ubuntu; see the [WSL2 GPU guide](WINDOWS-WSL2-GPU-GUIDE.md).
   With an AMD GPU (and no NVIDIA driver), the model runs in Lemonade Server on Windows, because Docker Desktop passes only NVIDIA GPUs into WSL containers. Setup reads the GPU and its memory in Windows, picks the model the native Windows installer would (for example `qwen3.5-9b` with 64K context on a 16 GB card), asks to install the pinned Lemonade Server for your Windows user, downloads the model once to `%LOCALAPPDATA%\ODS\lemonade\models` (checksum verified), and runs Lemonade on `127.0.0.1` through the `ODSLemonadeRuntime-<Windows SID>` scheduled task, which starts at sign-in. An existing Lemonade Server install is reused, including 10.7+ releases (configured through Lemonade's local API). Lemonade gets port 8080, or the first free one of 13305, 8000, 18080, 28080 when another program holds it; set `AMD_INFERENCE_PORT` to choose one. It loads the model on the GPU before Ubuntu is touched, then passes `--lemonade-url`, `--lemonade-model` and the GPU tier to the Linux installer; containers reach Lemonade at `host.docker.internal`. An AMD GPU with under 4 GB, or declining Lemonade, keeps the CPU route.
6. **ODS.** The Linux installer runs with `--pixel --no-hermes --no-openclaw`. When Ubuntu asks for your `[sudo] password`, type the Ubuntu password; nothing appears while you type.
7. **Verification.** The wrapper verifies Pixel gateway/ingress services, private ingress health, the dashboard HTTP endpoint and the authenticated Portal availability API. A dashboard that opens while its agent is unavailable is a failed verification. On success it opens Portal and creates an **ODS Portal** desktop shortcut (not in `-NonInteractive` runs). Send a message in Portal to verify model generation too.

`-NonInteractive` never installs prerequisites or changes Docker Desktop settings; it only checks them. Never send passwords through chat.

## Options and location

Use `wsl -l -v` to find distribution names. For an existing Ubuntu:

```powershell
.\install.ps1 -Distro Ubuntu
```

The runtime normally lives at `~/ods` inside Ubuntu. The ZIP is the source checkout;
verified setup copies the Windows lifecycle controller into private durable state
under `%LOCALAPPDATA%\ODS\wsl`, so sign-in recovery does not depend on the ZIP.
A custom runtime path must be an absolute Linux path:

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
- `-StateRoot <Windows path>`: optional private directory for the Windows WSL lifetime controller. Keep the same value for setup reruns and lifecycle commands. ODS enforces its owner ACLs. If a packaged terminal's scheduled process cannot see the default AppData controller, use a directory in Documents, for example `-StateRoot "$env:USERPROFILE\Documents\ODS-wsl-state"`; do not move an active controller's state without releasing it first.
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

Open the printed dashboard URL, normally **http://localhost:3001**, check availability and send a message in Portal. **http://localhost:3000** serves separate Open WebUI only when selected. Inside Ubuntu:

```bash
cd ~/ods
./ods status
sudo systemctl status openclaw-gateway.service pixel-ingress.service --no-pager
```

For failures, inspect the installer log and `sudo journalctl -u openclaw-gateway.service -u pixel-ingress.service -n 80 --no-pager`. If systemd is missing, enable `systemd=true` under `[boot]` in `/etc/wsl.conf`, preserving other settings, then run `wsl --terminate Ubuntu-24.04` in PowerShell and reopen Ubuntu.

## After sign-in and intentional stops

After installation verification succeeds, setup registers an owner-only Windows
sign-in task for the exact Windows account, registered Ubuntu name and ODS runtime
directory. It starts Docker Desktop using the executable verified during setup,
waits up to ten minutes for Docker and Compose inside that distribution, then
starts the existing ODS stack. Login remains usable while it waits. The complete
startup attempt has a twenty-minute budget; failures remain visible in
`startup-status.json` under `%LOCALAPPDATA%\ODS\wsl\<installation-id>`.

The controller stores whether ODS should be running. From the extracted source
directory, use the helper below, replacing the Ubuntu username and distribution
with the installation's values:

```powershell
& .\ods\installers\wsl-lifecycle.ps1 -Action status -Distro Ubuntu-24.04 -InstallRoot /home/YOUR_UBUNTU_USER/ods
& .\ods\installers\wsl-lifecycle.ps1 -Action stop -Distro Ubuntu-24.04 -InstallRoot /home/YOUR_UBUNTU_USER/ods
& .\ods\installers\wsl-lifecycle.ps1 -Action start -Distro Ubuntu-24.04 -InstallRoot /home/YOUR_UBUNTU_USER/ods
```

Use `-Action stop` to stop ODS and disable its next
automatic return, or `-Action start` to start it and enable return. `restart`
leaves return enabled; `release` disables return and releases only the owned WSL
client. These commands never terminate another distribution. A setup rerun keeps
an existing explicit stop preference. Stopping containers manually or closing an
Ubuntu window does not change this Windows startup preference.

`-Action status` reports the preference and the most recent startup result. The
durable `startup.ps1` path printed after successful setup accepts the same
`-Distro` and `-InstallRoot` arguments, so it can be used after deleting the source
ZIP. A `started` result means the stack-start operation completed; check Portal
availability and send a message to confirm model generation. The startup
coordinator does not resume an AMD model deliberately unloaded in **Models**;
use **Resume model** there when needed.

If stop reports that an earlier command is still draining, its stopped preference
has already been saved; wait for that command to finish and retry stop. If the
controller reports an unconfirmed Linux completion, it blocks further stack
changes. Restart Windows using **Restart**, then retry the requested action. A
new Windows boot proves that the old Linux command has ended; signing out is not
enough. Do not delete `command-pending.json`. Ordinary completed command errors
can be corrected and retried without restarting Windows.

## GPU placement

Pixel is the agent, not the model server. NVIDIA runs the model inside WSL (Docker Desktop's NVIDIA runtime). AMD runs it in Lemonade Server on Windows (see step 5); ROCm is not used in WSL. Without a usable GPU the model runs on the CPU. See [WSL2 GPU guide](WINDOWS-WSL2-GPU-GUIDE.md).

For AMD, Windows setup automatically passes `--lemonade-host-transport model-router` to the Linux installer and saves `LEMONADE_HOST_TRANSPORT=model-router` in the runtime `.env`. Windows and Ubuntu can have different localhost listeners. The WSL host agent therefore checks the Windows model through this installation's running model-router container, using its configured `host.docker.internal` endpoint. Before sending a request, it checks the container's ODS labels, installation mounts and Lemonade endpoint. Missing or mismatched ownership keeps the route unverified; model identity, context and a successful completion are still required for readiness.

Lemonade stays bound to Windows `127.0.0.1`; this transport does not enable LAN access or select cloud inference. The Linux setting `LEMONADE_EXTERNAL=true` describes where Lemonade runs, outside the Linux stack on the same Windows computer. Permission to manage it is verified separately against the ODS task and its installation binding. Other Lemonade installations use the default `--lemonade-host-transport direct`, which probes from the host agent's own network context.

The `ODSLemonadeRuntime-<Windows SID>` task starts at Windows sign-in when its
saved preference is running, restores the selected model and context, and
verifies the loaded model before setup proceeds. Its launcher and configuration
live in `%LOCALAPPDATA%\ODS\lemonade\portal-runtime`, so removing the temporary
installer checkout does not break the next startup. Startup failures are recorded
in `lemonade-launch.log` there, with three bounded Scheduler retries. After these
retries expire, inspect that log and retry the existing task from normal
PowerShell:

```powershell
$odsSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Start-ScheduledTask -TaskPath '\' -TaskName "ODSLemonadeRuntime-$odsSid"
```

This retries failed automatic startup. For a model deliberately unloaded in
**Models**, use **Resume model**, which validates and re-enables the owned runtime.
Starting the WSL stack does not change the Windows model's stopped preference.

After a Windows restart, sign in and let the startup coordinator connect Docker
to Ubuntu, then check Portal availability and send a message again. A registered
task or a healthy Lemonade API alone does not prove that model generation resumed
successfully.

## Manage AMD models from Portal

With the Windows ODS task bound to this Ubuntu distribution and ODS runtime directory, open **Models** in Portal or the Dashboard. The installer registers `%LOCALAPPDATA%\ODS\lemonade\models` as the shared model store; catalog and Hugging Face GGUF downloads go there, with progress, cancellation and checksum verification. They do not require another copy inside Ubuntu.

After a download is verified, use **Run** and choose its context. **Configure context** changes the active model through the same verified activation flow. ODS updates the Windows startup selection and the route used by Portal and ODS apps. Model architecture, memory, context and app compatibility still determine whether a particular GGUF can run.

**Unload model** stops the owned runtime to release GPU memory and keeps the saved model selection. Portal remains paused while inference is stopped. Use **Resume model** to restore the saved model and verify its route before changing models or context again.

These controls appear only after ODS verifies the task, Windows account, WSL installation and registered model store. For an older ODS task created without this binding, rerun the current Windows installer for the same distribution and runtime directory. Do not create the binding by editing runtime files. An independent Lemonade service remains external: change its model in Lemonade, then use **Adopt loaded model** to update the ODS route. Adoption does not grant runtime control or change Lemonade's startup selection. See [Model Management](MODEL-MANAGEMENT.md#windows-amd-with-portal-in-wsl) for details.

## Existing native Windows installations

Setup detects native runtimes at `ODS_HOME` or `%USERPROFILE%\ods` and stops to avoid competing stacks. Check any older custom location yourself. No data migration or deletion is automatic. Preserve needed data and migrate/remove the old deployment before switching; removal is destructive and your explicit choice.

Manage existing native installations using their own `ods.ps1`. The native implementation remains at `ods/installers/windows/install-windows.ps1` for maintenance, not the recommended new-install path. Native commands do not manage the WSL runtime.

### Moving from v2.6.0

First establish whether v2.6.0 is native Windows or already installed inside WSL.
The new PowerShell entry point is not an automatic native-to-WSL data converter.

For **native Windows**, keep the old deployment until you have a recoverable
backup of its runtime directory, private configuration, model files and Docker
volume data, plus application-level exports of histories or workflows you need.
Record its exact version/source and startup tasks. Do not paste credentials into
diagnostic logs. Stop the old deployment with its own CLI and retire only its
verified startup entries and Compose resources before creating the WSL stack;
stopped containers can still trigger the related-install guard. Do not reset
Docker Desktop, delete unrelated volumes, or bypass the conflict guard to run
both deployments on the same ports/project.

After preserving that backup, use the old deployment's supported removal flow
if you choose to retire it. Install into the chosen Ubuntu distribution and a
Linux runtime directory. Restore supported application exports deliberately;
do not copy a native Windows `.env` wholesale into WSL because paths, endpoints
and runtime ownership differ. The installer does not automatically import old
application histories, volumes or native model registrations. Keep the backup
until Portal, required apps, model changes and a full Windows sign-in cycle are
verified. For rollback, stop the new WSL deployment first, then restore the old
version and its matching data/volumes; never start both stacks together.

For **an existing WSL installation**, rerun setup for that same distribution and
absolute Linux runtime directory. The Linux installer updates that directory and
preserves its data and secrets; retain a backup before the update. It does not
upgrade an Ubuntu 22.04 distribution in place: select a qualified Ubuntu
24.04/26.04 distribution and plan data migration separately. NVIDIA still needs
the Windows driver and Docker GPU checks described above. A successful update
does not replace the post-sign-in generation check on that computer.

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

For a Windows-bound WSL installation, uninstall first validates its Windows
startup ownership. Before removing Pixel, it disables and settles the matching
WSL sign-in task and, on AMD, stops and disables only the Lemonade task bound to
that installation. If Pixel cleanup then fails, the remaining installation is
retained for recovery and Windows startup stays disabled. A busy or
unverifiable controller stops removal with an error; resolve it and retry instead
of bypassing the check. Another account's tasks and independent Lemonade services
are preserved. The Lemonade application and downloaded Windows models remain
available for reuse. Removing those separately through Windows Settings or
deleting model files is an additional, explicit choice.

References: [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands), [Docker WSL integration](https://docs.docker.com/desktop/features/wsl/).
