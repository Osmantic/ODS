# ODS Installation Troubleshooting Guide

This guide provides solutions for common issues encountered during the installation of ODS.

For **Linux**, run `./scripts/linux-install-preflight.sh` (or `./ods-preflight.sh --install-env`) for a structured report with stable check IDs; see [LINUX-TROUBLESHOOTING-GUIDE.md](LINUX-TROUBLESHOOTING-GUIDE.md) for ID-by-ID fixes.

## Linux Installer Startup

### Problem: Installer Stops Near System Detection With `No module named 'yaml'`
**Solution:** Deactivate Conda/venv before installing, or install PyYAML into the active Python.

ODS prefers the system Python during Linux install because distro packages such as `python3-yaml` are installed for `/usr/bin/python3`. If a Conda or venv Python is first in `PATH`, it may not see those modules.

```bash
conda deactivate
./install.sh
```

Or:

```bash
python3 -m pip install pyyaml
./install.sh
```

### Problem: `--non-interactive` Appears Hung During Docker Or NVIDIA Setup
**Solution:** Cache sudo credentials before starting, or run interactively.

```bash
sudo -v
./install.sh --non-interactive
```

## Docker Issues

### Container build DNS

If dashboard, dashboard-api, or Pixel preview-inspection image builds fail with
`EAI_AGAIN`, `ENOTFOUND`, `Temporary failure resolving`, or `Temporary failure in
name resolution`, inspect DNS in the **selected build environment**. The host
connectivity preflight and a successful image pull do not prove that a build's
`RUN npm ci`, `apt-get`, or `pip` step can resolve names. npm's `Exit handler never
called!` message alone does not establish a DNS problem.

ODS keeps the per-service Compose build log beside the install log and prints
its path on failure. Pixel's image build streams its output above the error.
Retain the first name-resolution error and its hostname; a later package error
can hide the cause. Remove credentials and private URLs before sharing logs.

1. Record the client/server versions (`docker version`), selected context
   (`docker context show`), builder (`docker buildx ls`, if available), and any
   `DOCKER_HOST` or `DOCKER_BUILDKIT` override. Check the daemon host, which may
   differ from the machine running the installer.
2. On a native Linux daemon host, inspect `/etc/resolv.conf`,
   `/run/systemd/resolve/resolv.conf`, and `resolvectl dns` when available. A
   `127.0.0.53` stub is normal on systemd-resolved hosts. Docker can select the
   real uplink resolver file; the stub's presence alone is not a fault.
3. Using an already cached image that includes `nslookup`, compare resolution
   of the **failing hostname** in a temporary container and a fresh `RUN` layer
   using the same builder and network as the failed build. For example, replace
   both placeholders before running:

   ```bash
   docker run --rm --pull=never <cached-image> nslookup <failing-hostname>
   ```

   This does not pull a diagnostic image. A missing image or lookup utility is
   not a DNS result. A successful container lookup does not prove BuildKit or a
   custom build network works. Check the resolver file inside the failing build
   environment and verify that its upstream addresses are reachable there.

If the selected native Linux Docker daemon uses an unreachable resolver, an
operator can add or edit its `dns` list in the daemon configuration (usually
`/etc/docker/daemon.json`), **preserving all other keys**, with resolver IPs
approved for and reachable from that network. Do not copy another host's LAN
address or assume a public resolver is permitted. For systemd-resolved, inspect
the real uplink settings instead of copying the loopback stub. Docker Desktop,
rootless Docker, remote daemons, and separate BuildKit builders have different
configuration locations; changing the installer's local file may do nothing.

Validate the configuration and schedule any required daemon restart with the
operator: a restart can interrupt other containers. Then repeat the failed
lookup/build and rerun the install. The installer guidance does not change DNS,
restart Docker, or certify recovery; the original build must succeed.

See Docker's [DNS resolver troubleshooting](https://docs.docker.com/engine/daemon/troubleshoot/#dns-resolver-issues)
and [BuildKit configuration](https://docs.docker.com/build/buildkit/configure/).

### Problem: Docker Not Installed
**Solution:** Install Docker by following the official [Docker installation guide](https://docs.docker.com/get-docker/).

### Problem: Docker Service Not Running
**Solution:** Start the Docker service.

```bash
sudo systemctl start docker
```

### Problem: Permission Denied When Running Docker Commands
**Solution:** Add your user to the Docker group.

```bash
sudo usermod -aG docker $USER
```
Then, log out and back in to apply the changes.

## GPU Detection Issues

### Problem: GPU Not Detected
**Solution:** Ensure that the NVIDIA drivers are installed and that the NVIDIA Container Toolkit is set up correctly.

Install NVIDIA Drivers:
```bash
sudo apt-get install nvidia-driver-<version>
```

Install NVIDIA Container Toolkit:
```bash
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
curl -s -L https://nvidia.github.io/libnvidia-container/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit
sudo systemctl restart docker
```

Verify GPU detection:
```bash
docker run --rm --gpus all nvidia/cuda:11.0-base nvidia-smi
```

### Problem: Blackwell GPU Shows Driver But No Devices
**Solution:** Install NVIDIA open kernel modules.

Blackwell GPUs, including RTX PRO 6000 Blackwell and Grace Blackwell systems, require the NVIDIA open kernel module driver stack. On Ubuntu 22.04/24.04:

```bash
sudo apt install nvidia-open
sudo reboot
```

If your distro uses branch-specific package names, install the matching open package, for example:

```bash
sudo apt install nvidia-driver-580-open
sudo reboot
```

## Port Conflicts

### Problem: Port Already in Use
**Solution:** Identify and stop the process using the port.

Find the process ID (PID) using the port:
```bash
sudo lsof -i :<port_number>
```

Stop the process:
```bash
sudo kill -9 <PID>
```

## Model Download Failures

### Problem: Model Download Fails Due to Network Issues
**Solution:** Ensure a stable internet connection and retry the download.

### Problem: Insufficient Disk Space
**Solution:** Free up disk space and retry the download.

Check disk usage:
```bash
df -h
```

## Health Check Timeouts

### Problem: Health Checks Fail Due to Timeout
**Solution:** Increase the timeout settings or check the server's health manually.

Increase timeout settings in the compose file (e.g., `docker-compose.base.yml`).

Check server health:
```bash
curl http://localhost:<port>/health
```
