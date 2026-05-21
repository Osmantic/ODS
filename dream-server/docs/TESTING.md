# Multi-Distro Testing Guide

Dream Server supports multiple Linux distributions. This guide covers how to test across distros efficiently.

## Real Hardware Fleet Validation

Dream Server also uses a private real-hardware fleet for release-readiness
evidence. CI and distrobox are useful for fast installer logic checks, but the
fleet is where fresh installs, Docker startup, GPU runtime behavior, dashboard
flows, Hermes auth, model switching, extension install paths, and agent
capabilities are exercised on real machines.

The sanitized public coverage is maintained in
[VALIDATION-MATRIX.md](VALIDATION-MATRIX.md). Release notes should cite the
fleet run date, hardware classes covered, regression replay result, and any
blocked, deferred, skipped, or not-run phases.

Volunteer and community testers are important for broader distro, GPU, driver,
and network coverage. Treat those reports as complementary breadth evidence;
the fleet is the repeatable parallel gate that can run whenever code changes.

Fleet phases currently include:

- regression replay for previously fixed fleet bugs;
- a constrained Apple Silicon smoke gate before parallel installs;
- read-only preflight snapshots for OS, RAM, disk, Docker, firewall, ports, and
  prior install state;
- non-interactive fresh installs from the public bootstrap path;
- core HTTP verification for dashboard-api, dashboard UI, llama-server, and
  Hermes proxy;
- dashboard model and extension flows;
- Hermes magic-link auth and seeded chat checks;
- Playwright dashboard UI checks;
- agent capability probes for chat, web search, files, code, skills, and model
  identity;
- opt-in lifecycle checks for reinstall, restart, and doctor behavior.

## Quick Reference

| Method | Speed | GPU Testing | Kernel Testing | Best For |
|--------|-------|-------------|----------------|----------|
| **Fleet harness** | 15-75 min | Yes | Yes | Release readiness on real heterogeneous hardware |
| **Fleet distro lab** | 5-20 min | No | Container: no / VM: yes | Multi-distro installer and Docker lifecycle coverage on tower2 |
| **Distrobox** | Instant (2s) | Yes | No | Daily dev, package manager validation |
| **Ventoy USB** | 5-10 min boot | Yes | Yes | Weekly full-stack validation |
| **CI Matrix** | Automatic | No | No | Every PR, syntax + detection checks |

## Fleet Distro Lab (tower2)

The fleet distro lab is the repeatable middle rung between CI containers and
full hardware fleet runs. `tower2` is provisioned with:

- Docker for fast disposable distro containers;
- Distrobox for interactive distro debugging;
- Incus + KVM/QEMU for disposable systemd-capable VMs.

Use the Docker runner for every fleet run:

```bash
cd ~/DreamServer/dream-server
tests/fleet-multi-distro.sh --pull
```

Run a focused subset while debugging:

```bash
tests/fleet-multi-distro.sh ubuntu/24.04 archlinux/current mint
tests/fleet-multi-distro.sh --no-dry-run ubuntu2404
```

The fast fleet matrix currently covers:

| Distro ID | Image | Package Manager |
|-----------|-------|-----------------|
| `ubuntu2404` | `ubuntu:24.04` | apt |
| `ubuntu2204` | `ubuntu:22.04` | apt |
| `debian12` | `debian:12` | apt |
| `mint213` | `linuxmintd/mint21.3-amd64:latest` | apt |
| `fedora41` | `fedora:41` | dnf |
| `rocky9` | `rockylinux:9` | dnf |
| `arch` | `archlinux:latest` | pacman |
| `manjaro` | `manjarolinux/base:latest` | pacman |
| `cachyos` | `cachyos/cachyos:latest` | pacman |
| `opensuse` | `opensuse/tumbleweed:latest` | zypper |

Aliases such as `ubuntu/24.04`, `ubuntu/22.04`, `debian/12`,
`fedora/41`, `archlinux/current`, `opensuse/tumbleweed`, and `mint` are
accepted by the runner for quick ad-hoc checks. The matrix uses Linux Mint
21.3 because the current Mint 22 Docker images report plain Ubuntu in
`/etc/os-release`, which is less useful for distro detection.

Use Incus VMs when a regression needs real systemd, boot, kernel, or Docker
daemon behavior:

```bash
incus launch images:ubuntu/24.04 ds-ubuntu2404 --vm -c limits.cpu=4 -c limits.memory=8GiB
incus exec ds-ubuntu2404 -- bash -lc 'cat /etc/os-release; systemctl is-system-running || true'
incus delete -f ds-ubuntu2404
```

For CachyOS, use the container matrix for package-manager coverage. Keep a
manual CachyOS VM template for systemd/kernel coverage because CachyOS publishes
installer ISOs rather than a standard Incus cloud image.

## Distrobox (Daily Testing)

Run any Linux distro as a container on your host machine. GPU passthrough works. No reboot needed.

### Setup (One-Time)

```bash
# Install distrobox
curl -s https://raw.githubusercontent.com/89luca89/distrobox/main/install | sudo sh

# Create test containers for target distros
distrobox create --name dream-test-fedora --image fedora:41
distrobox create --name dream-test-arch --image archlinux:latest
distrobox create --name dream-test-manjaro --image manjarolinux/base:latest
distrobox create --name dream-test-cachyos --image cachyos/cachyos:latest
distrobox create --name dream-test-opensuse --image opensuse/tumbleweed:latest
distrobox create --name dream-test-debian --image debian:12
distrobox create --name dream-test-ubuntu2204 --image ubuntu:22.04
distrobox create --name dream-test-mint213 --image linuxmintd/mint21.3-amd64:latest
distrobox create --name dream-test-rocky9 --image rockylinux:9
```

### Usage

```bash
# Switch to any distro instantly
distrobox enter dream-test-fedora
# You're now in Fedora with dnf, GPU visible
cd ~/dream-server
./install.sh --dry-run

# Exit and switch
exit
distrobox enter dream-test-arch
```

### What Distrobox CAN Test

- Package manager detection (`apt` vs `dnf` vs `pacman` vs `zypper`)
- `/etc/os-release` parsing and distro identification
- Tool availability and installation (`curl`, `jq`, `rsync`, `git`)
- Installer phase logic, error messages, and tier mapping
- Service registry loading and compose file generation
- GPU device visibility (`/dev/dri`, `/dev/nvidia*`)

### What Distrobox CANNOT Test

- Kernel module loading (`modprobe`, `sysctl` tuning)
- Real Docker-in-Docker service startup
- NVIDIA driver installation flow
- Secure Boot interactions
- System tuning file deployment (`/etc/modprobe.d/`, `/etc/sysctl.d/`)

For these, use Ventoy.

## Ventoy USB (Weekly Validation)

Boot any Linux distro from a single USB drive. Pick from a menu, boot into a live session, test with real GPU access.

### Setup (One-Time)

1. Get a **64GB+ USB 3.2** drive (boot speed matters)
2. Download Ventoy from [ventoy.net](https://ventoy.net)
3. Install Ventoy on the USB (this formats it)
4. Copy ISO files onto the USB partition — it's just a normal filesystem

### Recommended ISOs

| Distro | Why | Package Manager |
|--------|-----|-----------------|
| Ubuntu 24.04 LTS | Primary target | apt |
| Ubuntu 22.04 LTS | Still widely used | apt |
| Fedora 41 | Popular with devs | dnf |
| CachyOS | Arch-based, issue #33 | pacman |
| openSUSE Tumbleweed | Rolling release | zypper |
| Debian 12 | apt but not Ubuntu | apt |
| Linux Mint 21.3 | Ubuntu derivative with `ID=linuxmint` | apt |
| Rocky Linux 9 | RHEL-family server baseline | dnf |
| Manjaro | Arch derivative with desktop-user reach | pacman |

Total: ~25GB for all ISOs.

### Testing Workflow

1. Plug USB into test machine (Strix Halo tower, NVIDIA tower, etc.)
2. Boot from USB (F12/F2 at POST)
3. Select distro from Ventoy menu
4. Live session boots with network access
5. Open terminal:
   ```bash
   git clone --depth 1 https://github.com/Light-Heart-Labs/DreamServer.git
   cd DreamServer
   ./install.sh
   ```
6. Note what breaks
7. Reboot, pick next distro, repeat

**Time per distro:** ~10-15 minutes.

### Ventoy Persistence (Optional)

To keep installed packages and configs across reboots:

1. Create a persistence file: `sudo dd if=/dev/zero of=/ventoy/persistence.dat bs=1G count=10`
2. Format it: `sudo mkfs.ext4 /ventoy/persistence.dat`
3. Configure in `ventoy.json`

## Automated Test Script

Run installer validation across all Distrobox containers automatically:

```bash
# Create all test containers
./tests/test-multi-distro.sh --create

# Run all distros
./tests/test-multi-distro.sh

# Run specific distros
./tests/test-multi-distro.sh fedora41 arch cachyos mint213

# Clean up
./tests/test-multi-distro.sh --cleanup
```

### Output Example

```
━━━ Testing: fedora41 ━━━
  [PASS] fedora41: /etc/os-release ID=fedora
  [PASS] fedora41: package manager detected correctly (dnf)
  [PASS] fedora41: curl available
  [SKIP] fedora41: no GPU devices visible (expected in rootless containers)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Multi-Distro Test Summary
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ✓ ubuntu2404: PASS (5 checks)
  ✓ debian12: PASS (4 checks)
  ✓ fedora41: PASS (4 checks)
  ✓ arch: PASS (3 checks)
  ✓ opensuse: PASS (4 checks)
```

## CI Matrix

Every PR automatically tests installer detection on 10 distros via GitHub Actions containers. See `.github/workflows/matrix-smoke.yml`.

**Tested per PR:**
- `/etc/os-release` parsing
- `packaging.sh` package manager detection
- `pkg_install` for core tools (`curl`, `jq`)
- Bash syntax validation on all scripts

## Adding a New Distro

1. Add the distro ID to `installers/lib/packaging.sh` in the `detect_pkg_manager()` case block
2. Add a test entry in `tests/test-multi-distro.sh` DISTROS array
3. Add a fleet entry in `tests/fleet-multi-distro.sh`
4. Add a CI matrix entry in `.github/workflows/matrix-smoke.yml`
5. Test with Distrobox: `distrobox create --name dream-test-newdistro --image newdistro:latest`
6. Run: `./tests/test-multi-distro.sh newdistro`
7. Run the fleet matrix path: `./tests/fleet-multi-distro.sh newdistro`
