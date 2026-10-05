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
& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    $odsSrc = Join-Path $env:TEMP ('ods-install-' + [guid]::NewGuid().ToString('N'))
    $odsZip = Join-Path $odsSrc 'ods-main.zip'
    New-Item -ItemType Directory -Path $odsSrc | Out-Null
    Invoke-WebRequest -UseBasicParsing 'https://github.com/Osmantic/ODS/archive/refs/heads/main.zip' -OutFile $odsZip
    Expand-Archive -LiteralPath $odsZip -DestinationPath $odsSrc
    $odsEntry = Join-Path $odsSrc 'ODS-main\install.ps1'
    if (-not (Test-Path -LiteralPath $odsEntry -PathType Leaf)) { throw 'The downloaded archive does not contain the ODS installer.' }
    Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
    & $odsEntry
}
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
   With an AMD GPU (and no NVIDIA driver), the model runs in llama.cpp's `llama-server.exe` (Vulkan) on Windows, because Docker Desktop passes only NVIDIA GPUs into WSL containers. Setup reads the GPU and its memory in Windows and picks the model the native Windows installer would. It asks to download the pinned llama.cpp Vulkan build from github.com/ggml-org into `%LOCALAPPDATA%\ODS\llama.cpp` (size and SHA-256 checked before extraction) and checks that it starts and sees the GPU (`--version`, `--list-devices`). It downloads the model once to `%LOCALAPPDATA%\ODS\lemonade\models` (checksum verified) and runs llama-server on `127.0.0.1`, with an API key, through the `ODSLlamaServerRuntime-<Windows SID>` scheduled task, which starts at sign-in. llama-server gets port 8080, or the first free one of 18080 and 28080 when another program holds it; set `AMD_INFERENCE_PORT` to choose one. Setup proves the model on the GPU before Ubuntu is touched, then passes the `--native-llm-*` options and the GPU tier to the Linux installer. The API key travels in an environment variable, never on a command line; containers reach the server at `host.docker.internal`. An AMD GPU without enough memory for a catalog model, declining the download, or no usable Vulkan device keeps the CPU route. An installation that ran Lemonade Server moves to llama.cpp on a rerun; see [AMD GPUs now run on llama.cpp](MIGRATION-LEMONADE-TO-LLAMACPP.md).
6. **ODS.** A new installation runs the Linux installer with `--pixel --no-hermes`. A rerun (an update) leaves out `--no-hermes`, so a Hermes that you added from the Extensions Library stays on. When Ubuntu asks for your `[sudo] password`, type the Ubuntu password; nothing appears while you type.
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
- `-NoHermes`: turns Hermes off, also on a rerun. Without it, a new installation starts without Hermes and a rerun keeps the choice made in the Extensions Library. `-Hermes` is rejected, and `-All` cannot enable it (a rerun with `-All` turns it off).
- `-OpenClaw`: accepted and ignored with a notice; the legacy OpenClaw extension was removed.

## Already inside Ubuntu?

With the same systemd/Docker prerequisites:

```bash
git clone https://github.com/Osmantic/ODS.git
cd ODS
bash install.sh --pixel --no-hermes
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
changes until a restart proves that the old Linux command has ended. Follow the
remedy in the message. Usually that is `wsl --shutdown`, then retry the
requested action: a new WSL VM boot is proof. Stop, release and uninstall never
start a stopped distribution for this check, so open the distribution again
before retrying them. A command recorded by an older ODS version needs a Windows
restart using **Restart**; signing out is not enough. Do not delete
`command-pending.json`. Ordinary completed command errors can be corrected and
retried without restarting Windows.

## GPU placement

Pixel is the agent, not the model server. NVIDIA runs the model inside WSL (Docker Desktop's NVIDIA runtime). AMD runs it in `llama-server.exe` on Windows (see step 5); ROCm is not used in WSL. Without a usable GPU the model runs on the CPU. See [WSL2 GPU guide](WINDOWS-WSL2-GPU-GUIDE.md).

For AMD, Windows setup automatically passes `--native-llm-host-transport model-router` to the Linux installer and saves `ODS_HOST_LLM_TRANSPORT=model-router` in the runtime `.env`. Windows and Ubuntu can have different localhost listeners. The WSL host agent therefore checks the Windows model through this installation's running model-router container, using its configured `host.docker.internal` endpoint. Before sending a request, it checks the container's ODS labels, installation mounts and llama-server endpoint. Missing or mismatched ownership keeps the route unverified; model identity, context and a successful completion are still required for readiness.

llama-server stays bound to Windows `127.0.0.1` and requires its API key; this transport does not enable LAN access or select cloud inference. Permission to manage the server is verified separately against the ODS task and its installation binding. Other installations use the default `--native-llm-host-transport direct`, which probes from the host agent's own network context.

The `ODSLlamaServerRuntime-<Windows SID>` task starts at Windows sign-in when
its saved preference is running, starts llama-server with the selected model and
context, and proves both before it reports ready. Its launcher and configuration
live in `%LOCALAPPDATA%\ODS\lemonade\portal-runtime`, so removing the temporary
installer checkout does not break the next startup. Startup failures are recorded
in `native-llama-launch.log` there, and llama-server's own output in
`llama-server.log`, with three bounded Scheduler retries. After these retries
expire, inspect those logs and retry the existing task from normal PowerShell:

```powershell
$odsSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
Start-ScheduledTask -TaskPath '\' -TaskName "ODSLlamaServerRuntime-$odsSid"
```

This retries failed automatic startup. For a model deliberately unloaded in
**Models**, use **Resume model**, which validates and re-enables the owned runtime.
Starting the WSL stack does not change the Windows model's stopped preference.

After a Windows restart, sign in and let the startup coordinator connect Docker
to Ubuntu, then check Portal availability and send a message again. A registered
task or a healthy llama-server alone does not prove that model generation resumed
successfully.

## Manage AMD models from Portal

With the Windows ODS task bound to this Ubuntu distribution and ODS runtime directory, open **Models** in Portal or the Dashboard. The installer registers `%LOCALAPPDATA%\ODS\lemonade\models` as the shared model store; catalog and Hugging Face GGUF downloads go there, with progress, cancellation and checksum verification. They do not require another copy inside Ubuntu.

After a download is verified, use **Run** and choose its context. **Configure context** changes the active model through the same verified activation flow. ODS updates the Windows startup selection and the route used by Portal and ODS apps. Model architecture, memory, context and app compatibility still determine whether a particular GGUF can run.

**Unload model** stops the owned runtime to release GPU memory and keeps the saved model selection. Portal remains paused while inference is stopped. Use **Resume model** to restore the saved model and verify its route before changing models or context again.

These controls appear only after ODS verifies the task, Windows account, WSL installation and registered model store. If they are missing, rerun the current Windows installer for the same distribution and runtime directory; it binds the task to that installation. Do not create the binding by editing runtime files. When ODS cannot prove that this installation manages the model server, **Models** says so and does not change its model. See [Model Management](MODEL-MANAGEMENT.md#windows-amd-with-portal-in-wsl) for details.

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

## Retained Pixel sandbox after recreating Ubuntu

Removing an Ubuntu distribution does not remove images from Docker Desktop.
If Pixel reports `Shared live sandbox tag exists without an active Pixel release`,
the shared `openclaw-sandbox:bookworm-slim` tag can still belong to the previous
installation. For example, its sandbox can be built for UID 1000 while the new
Ubuntu account is UID 1001. A matching Pixel version alone is insufficient.
This is separate from the `unsafe-inspection-docker` executable-permissions error.

In the new Ubuntu terminal, inspect the engine, current account, image and all
containers using that image (including stopped containers):

```bash
id -u
docker info --format '{{.ID}}'
docker image inspect openclaw-sandbox:bookworm-slim \
  --format 'ID={{.Id}} tags={{json .RepoTags}} user={{.Config.User}} labels={{json .Config.Labels}}'
docker ps -a --no-trunc --filter ancestor=openclaw-sandbox:bookworm-slim \
  --format '{{.ID}} {{.Names}} {{.Status}}'
```

No listed containers is necessary but does not prove that another WSL
installation has stopped using this shared tag. Check those installations too.
If any still uses it, stop here and retain the tag; use a separate Docker engine
for the new deployment or retire the old deployment through its own uninstall.

Only when you have confirmed the old installation is retired, preserve its exact
image under another tag before removing **only the old shared tag**. Replace
the placeholder below with the complete `sha256:...` ID from inspection. The
retention tag includes that immutable image ID. Keep other Docker/Pixel
installation work stopped during this operation: Docker does not provide an
atomic compare-and-remove operation for tags shared by several installations.
The checks below reject changed identities and any remaining containers.

```bash
(
  set -euo pipefail
  old_id='sha256:PASTE_FULL_64_HEX_ID'
  [[ "$old_id" =~ ^sha256:[a-f0-9]{64}$ ]] || { echo 'Enter the full old image ID.' >&2; exit 1; }
  live='openclaw-sandbox:bookworm-slim'
  retained="pixel-sandbox-retained:sha256-${old_id#sha256:}"
  engine=$(docker info --format '{{.ID}}')
  [[ -n "$engine" ]]
  [[ $(docker image inspect "$live" --format '{{.Id}}') == "$old_id" ]]
  consumers=$(docker ps -aq --filter "ancestor=$old_id")
  [[ -z "$consumers" ]] || { echo 'Containers still reference this image; nothing changed.' >&2; exit 1; }
  if docker image inspect "$retained" >/dev/null 2>&1; then
    [[ $(docker image inspect "$retained" --format '{{.Id}}') == "$old_id" ]]
  else
    docker image tag "$old_id" "$retained"
  fi
  [[ $(docker image inspect "$retained" --format '{{.Id}}') == "$old_id" ]]
  [[ $(docker info --format '{{.ID}}') == "$engine" ]]
  [[ $(docker image inspect "$live" --format '{{.Id}}') == "$old_id" ]]
  consumers=$(docker ps -aq --filter "ancestor=$old_id")
  [[ -z "$consumers" ]]
  docker image rm "$live"
  printf 'Old image preserved as %s. Rerun the ODS installer.\n' "$retained"
)
```

Do not use `--force`, remove the image by ID, prune images/volumes, or reset
Docker Desktop. The retention tag keeps the old image available. Rerun the same
ODS installation command: Pixel must validate and activate the new account's
candidate itself. Do not retag the candidate manually or edit the UID labels.
If image removal fails or any identity changed during review, stop and inspect
the current state instead of retrying with force.

## Uninstall WSL ODS

Inside Ubuntu, use your chosen runtime directory:

```bash
cd ~/ods
./ods-uninstall.sh --force
```

Do not unregister Ubuntu to remove only ODS.

For a Windows-bound WSL installation, uninstall first validates its Windows
startup ownership. Before removing Pixel, it disables and settles the matching
WSL sign-in task and, on AMD, stops and disables only the llama.cpp task bound to
that installation. If Pixel cleanup then fails, the remaining installation is
retained for recovery and Windows startup stays disabled. A busy or
unverifiable controller stops removal with an error; resolve it and retry instead
of bypassing the check. Another account's tasks and model servers that ODS does
not manage are preserved. The disabled task, the llama.cpp runtime in
`%LOCALAPPDATA%\ODS\llama.cpp` and the downloaded Windows models remain on
Windows. Deleting the task or those files is an additional, explicit choice.

References: [Microsoft WSL commands](https://learn.microsoft.com/en-us/windows/wsl/basic-commands), [Docker WSL integration](https://docs.docker.com/desktop/features/wsl/).
