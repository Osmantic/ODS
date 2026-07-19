# Linux troubleshooting guide

Structured reference for **Linux install and runtime** issues. When you run `./scripts/linux-install-preflight.sh` or `./ods-preflight.sh --install-env`, each line uses a **check ID**. Use this document to jump from an ID to likely causes and fixes.

**Related:** [INSTALL-TROUBLESHOOTING.md](INSTALL-TROUBLESHOOTING.md), [TROUBLESHOOTING.md](TROUBLESHOOTING.md), [LINUX-PORTABILITY.md](LINUX-PORTABILITY.md), [FIELD-INSTALL-REPORT-LINUX.md](FIELD-INSTALL-REPORT-LINUX.md).

---

## Using the install preflight

From the `ods` directory:

```bash
./scripts/linux-install-preflight.sh
./scripts/linux-install-preflight.sh --json
./scripts/linux-install-preflight.sh --json-file /tmp/preflight.json
./ods-preflight.sh --install-env --json   # same as linux-install-preflight.sh
```

- **`--strict`** — exit with failure if any check is **warn** or **fail** (useful in automation).
- **`--ods-root PATH`** — directory used for the disk-space probe (default: ods root).
- **`--min-disk-gb N`** — minimum free space in GB to treat as OK (default: 15).

JSON output includes `schema_version`, `kind: linux-install-preflight`, `distro`, `kernel`, `checks[]`, and `summary`.

---

## Check IDs (alphabetical)

### CGROUP_V2

**Symptoms:** Warning that cgroup v2 was not detected.

**Typical causes:** Older kernels, unusual container hosts, or mis-mounted `/sys/fs/cgroup`.

**Fixes:**

- On normal desktop/server distros from the last several years, cgroup v2 is standard; if Docker works, you can ignore this warning.
- If Docker fails with cgroup-related errors, ensure you are not mixing rootless Docker with a broken delegated cgroup setup. See your distro’s Docker documentation.

---

### COMPOSE_CLI

**Symptoms:** **Fail** — neither `docker compose` nor `docker-compose` works.

**Typical causes:** Docker Engine installed without the Compose v2 plugin; very old Docker packages; PATH issues.

**Fixes:**

- Install **Docker Compose v2** (plugin): often package `docker-compose-plugin` (Debian/Ubuntu) or equivalent.
- Legacy: install standalone `docker-compose` v1 if you must (less ideal).

Verify:

```bash
docker compose version
# or
docker-compose version
```

---

### COMPOSE_FILES

**Symptoms:** Warning — `docker-compose.base.yml` / `docker-compose.yml` missing under the ods tree.

**Typical causes:** Running the script from the wrong directory; incomplete checkout.

**Fixes:**

- `cd` into the extracted **ods** directory that contains the compose files from the release or git clone.
- Re-download or re-clone the repository if files are missing.

---

### CURL_INSTALLED

**Symptoms:** **Warn** — `curl` not in PATH.

**Typical causes:** Minimal container or netinst image without `curl`.

**Fixes:**

```bash
# Debian/Ubuntu
sudo apt update && sudo apt install -y curl

# Fedora
sudo dnf install -y curl

# Arch
sudo pacman -S curl
```

The installer and many health checks expect `curl`.

---

### DISK_SPACE

**Symptoms:** **Warn** — low free space on `--ods-root` (or default ods root).

**Typical causes:** Small root partition; large existing Docker data; wrong path passed to `--ods-root`.

**Fixes:**

- Free space or move the ODS data directory to a larger volume (see installer docs for `data/` layout).
- Point `--ods-root` at the filesystem you intend to use for the install.

---

### DISTRO_INFO

**Symptoms:** **Fail** — `/etc/os-release` missing.

**Typical causes:** Non-Linux environment; severely broken chroot.

**Fixes:**

- Run on a supported Linux distribution. For Windows, use the Windows installer + WSL2; for macOS, use the macOS path.

---

### DOCKER_DAEMON

**Symptoms:** **Fail** — `docker info` does not succeed.

**Typical causes:** Docker service stopped; user lacks permission to the Docker socket; rootless Docker socket not in environment.

**Fixes:**

```bash
# systemd
sudo systemctl start docker
sudo systemctl enable docker

# Permission denied on /var/run/docker.sock
sudo usermod -aG docker "$USER"
# then log out and back in (or newgrp docker)
```

Confirm:

```bash
docker info
docker run --rm hello-world
```

---

### DOCKER_INSTALLED

**Symptoms:** **Fail** — `docker` command not found.

**Typical causes:** Docker Engine never installed; PATH not including Docker binaries.

**Fixes:**

- Install [Docker Engine](https://docs.docker.com/engine/install/) for your distro.
- Ensure `/usr/bin` (or wherever `docker` lives) is on your `PATH`.

---

### KERNEL_INFO

**Symptoms:** Always **pass** — prints `uname -r` (informational).

**Note:** Use this value when comparing against [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md) or when filing bugs.

---

### JQ_INSTALLED

**Symptoms:** **Warn** — `jq` not installed.

**Typical causes:** Minimal system; skipped optional packages.

**Fixes:**

- The ODS installer often installs `jq` automatically when possible. You can install manually:

```bash
sudo apt install -y jq   # Debian/Ubuntu
sudo dnf install -y jq   # Fedora
```

---

### KERNEL_INFO

**Symptoms:** Always **pass** with kernel version (informational).

**Note:** Very old kernels may be incompatible with modern Docker or NVIDIA drivers; if you hit exotic bugs, compare with [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md).

---

### NVIDIA_CONTAINER_RUNTIME

**Symptoms:** **Warn** — `nvidia-smi` works but Docker does not show an NVIDIA runtime.

**Typical causes:** NVIDIA drivers installed but [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) not configured for Docker.

**Fixes:**

- Install and configure `nvidia-container-toolkit`, then restart Docker:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
```

- Verify: `docker info | grep -i nvidia`

If you are intentionally **CPU-only**, you can ignore this after setting `GPU_BACKEND=cpu` in your capability/install profile.

---

### ROOTLESS_SUBUID

**Symptoms:** **Fail** — Docker is running in rootless mode but `/etc/subuid` or `/etc/subgid` is missing an entry for the current user, or the allocated range is smaller than 65 536 IDs.

**Typical causes:**

- The user was created without subordinate UID/GID ranges (common on minimal or cloud images).
- The ranges were assigned manually with a count below 65 536 (the minimum needed for a full Linux user namespace).
- `/etc/subuid` or `/etc/subgid` does not exist at all on the host.

**Why this matters:** Docker rootless mode uses Linux user namespaces to map container UIDs into unprivileged host ranges. Services such as n8n, Whisper, TTS, privacy-shield, and others run as non-root users inside containers and require a minimum of 65 536 subordinate IDs. Without a sufficient mapping, container startup fails with errors like `failed to register layer: uid/gid map out of range`.

**Fix:**

```bash
# Allocate 65 536 subordinate IDs starting at 100000 for the current user
sudo usermod \
  --add-subuids 100000-165535 \
  --add-subgids 100000-165535 \
  "$USER"
```

Verify:

```bash
grep "^$USER:" /etc/subuid
grep "^$USER:" /etc/subgid
# Expected output: <username>:100000:65536 (or any range with count ≥ 65536)
```

Then restart the rootless Docker service so the new mappings take effect:

```bash
systemctl --user restart docker
```

If the files do not exist at all, install the subordinate ID support package first:

```bash
# Debian/Ubuntu
sudo apt install -y uidmap

# Fedora/RHEL
sudo dnf install -y shadow-utils
```

**Note:** This check is skipped when Docker is not running in rootless mode. Standard (daemon-level) Docker installations use a different user namespace strategy and do not require `/etc/subuid`/`/etc/subgid` entries.

---

## Common cross-cutting issues

### Firewall blocking local ports

If the dashboard or WebUI is unreachable from another machine, check `ufw` / `firewalld` / `iptables` for the ports in `config/ports.json` (or your `.env` overrides).

### SELinux / AppArmor

Rare compose failures can be policy-related. Check audit logs; temporarily testing with permissive profiles is diagnostic only — prefer documented volume labels and permissions.

### Docker BuildKit / proxy

Corporate proxies may require `HTTP_PROXY` / `HTTPS_PROXY` in Docker’s systemd drop-in or `~/.docker/config.json` for image pulls.

---

## Getting help

When opening an issue, attach a **field install report** (see [FIELD-INSTALL-REPORT-LINUX.md](FIELD-INSTALL-REPORT-LINUX.md)) and the JSON from:

```bash
./scripts/linux-install-preflight.sh --json
```

Redact secrets, internal hostnames, and paths you do not want to share.
