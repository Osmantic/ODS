# Dream Server Installation Troubleshooting Guide

This guide provides solutions for common issues encountered during the installation of Dream Server.

For **Linux**, run `./scripts/linux-install-preflight.sh` (or `./dream-preflight.sh --install-env`) for a structured report with stable check IDs; see [LINUX-TROUBLESHOOTING-GUIDE.md](LINUX-TROUBLESHOOTING-GUIDE.md) for ID-by-ID fixes.

## Linux Installer Startup

### Problem: Installer Stops Near System Detection With `No module named 'yaml'`
**Solution:** Deactivate Conda/venv before installing, or install PyYAML into the active Python.

Dream Server prefers the system Python during Linux install because distro packages such as `python3-yaml` are installed for `/usr/bin/python3`. If a Conda or venv Python is first in `PATH`, it may not see those modules.

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
