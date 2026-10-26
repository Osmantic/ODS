# ODS Troubleshooting

Common issues and solutions.

---

## Installation Issues

### Docker Permission Denied

**Error:** `permission denied while trying to connect to the Docker daemon`

**Fix:**
```bash
# Add yourself to docker group
sudo usermod -aG docker $USER

# Log out and back in (or reboot)
# Verify with:
docker ps
```

### Pixel Cannot Access Docker

**Error:** `Pixel's Docker access check failed` during Docker setup, or
`Docker daemon is unavailable to the current user` in Pixel's bootstrap log.

Pixel runs Docker as the ODS install owner. The rest of the installer may be
able to use `sudo docker` even when that owner's Docker access fails. Current
Linux/WSL installers check the owner's access before downloading images and
show Docker's error in the terminal. This check also applies with `--skip-docker`;
that flag skips installing Docker, not Pixel's runtime requirements.

In a terminal as the owner named in the error, run:

```bash
docker info >/dev/null
```

Run it without `sudo`. It queries Docker without changing containers and keeps
the error visible while hiding the full info report. Use the reported error:

- **Permission denied:** for a system Docker Engine, check the owner's socket
  access and [Docker's Linux post-install instructions](https://docs.docker.com/engine/install/linux-postinstall/).
  Docker group membership grants root-level privileges; only grant it deliberately.
  If membership was just changed, open a new login session.
- **Cannot connect:** check that the intended daemon is running. On WSL, check
  Docker Desktop and its integration with the selected Ubuntu distribution.
- **Context, endpoint or TLS error:** check `docker context show`, `DOCKER_CONTEXT`,
  `DOCKER_HOST` and `DOCKER_CONFIG` in that user's session. Correct the setting
  identified by Docker rather than changing socket permissions.

Once the command succeeds, rerun the documented installer with the same install
directory and options. If you previously chose `--no-pixel`, replace it with
`--pixel` to add Pixel. This error alone does not call for `--force`, deleting
the ODS directory, or removing containers or volumes.

If the check succeeds but bootstrap still fails, it does not explain the earlier
failure. Keep the first error and `logs/pixel-install.log` from that attempt;
review them for credentials or private endpoints before sharing. Avoid repeated
reinstalls based only on the generic Docker message.

### NVIDIA Container Toolkit Missing

**Error:** `could not select device driver "" with capabilities: [[gpu]]`

**Fix:**
```bash
# Install NVIDIA Container Toolkit
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -s -L https://nvidia.github.io/libnvidia-container/$distribution/libnvidia-container.list | \
    sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
    sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

### AMD GPU Devices Missing in LXD/LXC

**Error:** installer reports missing `/dev/kfd`, `/dev/dri`, or `/dev/dri/renderD*`, or AMD services never become healthy inside an LXD container.

**Cause:** LXD/LXC containers can expose enough host CPU/sysfs information for ODS to recognize AMD/Strix Halo hardware while still hiding the actual GPU device nodes that Docker must mount into the AMD containers: `/dev/dri` for the default Vulkan llama.cpp image, and `/dev/kfd` as well for the ROCm image and ComfyUI.

**Fix for GPU acceleration:** pass the GPU devices from the LXD host into the container, then re-run the installer.

```bash
lxc config set <container> security.nesting=true
lxc config device add <container> gpu gpu
lxc config device add <container> kfd unix-char path=/dev/kfd
```

**CPU fallback:** if you do not need GPU acceleration inside the container, run:

```bash
GPU_BACKEND=cpu ./install.sh
```

Fresh installs now fall back to CPU mode automatically when AMD is auto-detected but the required device nodes are unavailable inside a container. If you explicitly force `GPU_BACKEND=amd`, the installer fails fast with passthrough guidance instead.

---

## Startup Issues

### llama-server Container Won't Start

Start in **Dashboard → Models** and check which runtime is selected. These
container commands apply to an ODS-managed Docker llama-server on Linux/WSL:

```bash
ods status
ods logs llama-server
```

Stop following logs with **Ctrl+C**. The `ods` CLI selects this installation's
Compose files; a bare `docker compose` command may select a different stack.

If inference runs natively on macOS or Windows, use the Models controls and the
[platform-specific model-management guidance](MODEL-MANAGEMENT.md#recommended-dashboard-models-page);
there may be no llama-server container. If **Models** reports an external or
remote API, inspect that provider and **Settings → Model connections** instead.
Restarting a local container does not repair the selected remote server.

Use the first reported error to choose the next step:

1. **Missing or incomplete model:** check free space with `df -h` and inspect
   download progress/error in **Models**. Resolve the reported disk, network or
   Hub-access problem, then retry **Download**. Wait for verification before
   choosing **Run**. Restarting inference does not complete a failed download.
2. **Insufficient memory:** follow [CUDA Out of Memory](#cuda-out-of-memory)
   below to select a suitable model/context through the managed controls.
3. **GPU unavailable:** run `ods doctor` and use the reported driver/device
   error. `nvidia-smi` is a driver check for NVIDIA hosts, not for AMD, Intel or
   Apple GPUs. See [Hardware Sizing](HARDWARE-GUIDE.md) for supported hardware.

If Portal reports an interrupted model switch, use **Recover model switch** or,
from the Linux/WSL Bash CLI, `ods model recover`. Recovery reconciles the existing switch;
it does not download or choose another model. If it refuses, keep the saved
state and follow its reported repair guidance. See
[Interrupted switches](PORTAL-MODEL-TRANSITIONS.md#interrupted-switches) before
attempting another activation; do not delete transaction journals to unlock it.

### Open WebUI Shows "No Models Available"

First inspect **Dashboard → Models**. A model may still be downloading/loading,
the selected runtime may have failed or stopped, or inference may be routed to
an external API. Follow the startup checks above for the runtime actually in use.

Wait while the download or runtime reports progress; loading time depends on the
model and hardware. Once Models reports the selected model running, send a short
message through Portal or an enabled chat app. If inference works there but
Open WebUI still has an empty list, inspect `ods logs open-webui` and its model
connection settings. See the [Open WebUI service guide](../extensions/services/open-webui/README.md).

### Port Already in Use

**Error:** `Bind for 0.0.0.0:3000 failed: port is already allocated`

**Fix:**
1. Find what's using the port:
   ```bash
   lsof -i :3000
   ```

2. Change port in `.env`:
   ```bash
   WEBUI_PORT=3001
   ```

3. Restart:
   ```bash
   docker compose down && docker compose up -d
   ```

---

## Runtime Issues

### CUDA Out of Memory

Identify the failing service in its logs first. ODS's local LLM runs on
llama.cpp; a `torch.cuda.OutOfMemoryError` from an image or speech extension
needs that extension's own memory settings. On an NVIDIA host, `nvidia-smi`
shows which processes use VRAM. On other backends, use their platform diagnostics.

For an ODS-managed local language model:

1. In **Models**, use **Configure context** on the running model and review the
   offered context sizes, memory estimates and compatibility warnings. Choose a
   smaller context only when it still meets the model's and enabled apps'
   requirements. A fixed 2K/4K context is not suitable for every Portal or Talk
   configuration.
2. If a suitable context does not fit, choose a smaller compatible model.
   **Download**, wait for verification, then **Run** it with the selected context.
   The managed activation updates the artifact, runtime and dependent app routes
   together. See [Model Management](MODEL-MANAGEMENT.md#recommended-dashboard-models-page).
3. For headless Linux/WSL maintenance, `ods model list` shows tier choices and marks
   downloaded artifacts. `ods model swap <tier>` activates an already-downloaded
   tier through the same host-agent transaction; it does not download a model or
   select an arbitrary GGUF file.

After activation, check the running model in **Models** and send a short message
through the affected chat app. Keep any reported activation or recovery error
visible; use the interrupted-switch guidance above if a switch remains pending.
For remote/external inference, change memory/context settings at the provider
that owns the model rather than editing this installation's local model fields.

### Responses Very Slow

**Possible causes:**

1. **First request (cold start):** Wait 30-60 seconds for model warm-up
2. **Swapping to disk:** Check `free -h` — if swap is heavily used, reduce context
3. **Network issue:** Verify GPU is being used: `watch nvidia-smi`

### Whisper Not Transcribing

**Check:**
```bash
docker compose logs whisper
```

**Common fixes:**
1. Whisper may need to download model on first use — wait
2. Check that Whisper is running: `docker compose ps whisper`
3. Check GPU memory — Whisper needs ~3GB for medium model

---

## Network Issues

### Can't Access WebUI Remotely

**By default, services bind to localhost only.**

To allow remote access:

1. **Warning:** Only do this on trusted networks!

2. Edit the compose file for your platform:
   - NVIDIA: `docker-compose.base.yml` + `docker-compose.nvidia.yml`
   - AMD Strix Halo: `docker-compose.base.yml` + `docker-compose.amd.yml`
   Then change ports, for example:
   ```yaml
   ports:
     - "0.0.0.0:3000:8080"  # Was "3000:8080"
   ```

3. Configure firewall:
   ```bash
   sudo ufw allow 3000
   ```

### Services Can't Communicate

**Check container network:**
```bash
docker network inspect ods-network
```

**Ensure all services use the same network** (default in our compose file).

---

## Data Issues

### Reset Everything

**Warning:** This deletes all data!

```bash
cd ~/ods  # or wherever you installed
./ods-uninstall.sh --force
./install.sh
```

### Backup Data

```bash
cd ~/ods
tar -czvf ods-backup-$(date +%Y%m%d).tar.gz data/
```

### Restore Data

```bash
cd ~/ods
docker compose down
tar -xzvf ods-backup-YYYYMMDD.tar.gz
docker compose up -d
```

---

## Extension runtime check (post-install)

After installation, phase 13 runs **manifest validation** and a **non-blocking** check of non-core services that have a compose fragment: it verifies whether each matching container exists, is running, and (when the manifest defines a health path and port) whether `http://127.0.0.1:<port><path>` responds.

**Run manually** from your install directory (e.g. `~/ods`):

```bash
bash scripts/extension-runtime-check.sh .
```

**Strict mode** (fails if any running service fails its health probe — useful in CI or scripted verification):

```bash
EXTENSION_RUNTIME_CHECK_STRICT=1 bash scripts/extension-runtime-check.sh .
```

If Docker is not running or not reachable, the script skips checks and exits successfully. Install **jq** and **curl** where possible so related tooling and health probes behave consistently.

If a service shows **running but health failed**, inspect logs and compose state:

```bash
cd ~/ods
docker compose ps
docker compose logs <service>
ods status
```

---

## Connected apps (Google Calendar and Gmail)

The card in **Settings, Connected apps** names the problem and its fix; these
are the questions it doesn't answer. The full guide is
[Connected apps](pixel/CONNECTED-APPS.md).

### Connected apps aren't in Settings

**Cause:** `PORTAL_CONNECTED_APPS_ENABLED=false` in ODS's `.env`.

**Fix:** set it to `true`, or remove the line, then reload the dashboard.

### Google says the app isn't verified

**Cause:** the sign-in app is your own, and Google hasn't reviewed it. That's expected.

**Fix:** choose **Advanced**, then continue to your app.

### The Google tab ends on a page that can't be reached

**Cause:** your browser is on a different computer from the one running ODS, and Google sent it back to `127.0.0.1` on that computer.

**Fix:** copy the whole address from that tab, paste it into the wizard, and press **Finish connecting**.

### Google signs Portal out every week

**Cause:** your Google app is still in Testing, and Google ends Testing sign-ins after 7 days.

**Fix:** open your app's **Audience** page in Google Cloud, press **Publish app**, then **Reconnect** in Settings.

### Portal says it can't read my calendar or email

**Cause:** Google isn't connected, needs reconnecting, or you didn't turn that part on.

**Fix:** open **Settings, Connected apps**. The card says which, and offers the button that fixes it. Portal's chat also shows **Open Connected apps** when the connection needs you.

Still stuck? Ask on the ODS Discord: <https://discord.gg/4ntNp9MAwC>.

---

## Getting Help

1. **Check logs:**
   ```bash
   docker compose logs -f
   ```

2. **Check container status:**
   ```bash
   docker compose ps
   ```

3. **Check GPU:**
   ```bash
   nvidia-smi
   ```

4. **Check disk:**
   ```bash
   df -h
   ```

5. **Open an issue:** https://github.com/Osmantic/ODS/issues

---

*Built by The Collective*
