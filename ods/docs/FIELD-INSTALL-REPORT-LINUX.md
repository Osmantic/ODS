# Field install report (Linux)

Use this template when reporting Linux install problems so maintainers can reproduce and classify issues quickly. **Do not paste secrets** (API keys, passwords, full `.env`).

## Environment

| Field | Your value |
|-------|------------|
| ODS version / git SHA | |
| Install command used | e.g. `./install.sh`, `./install.sh --dry-run` |
| Distribution | Paste **PRETTY_NAME** and **VERSION_ID** from `/etc/os-release` |
| Kernel | Output of `uname -a` |
| Architecture | e.g. `x86_64`, `aarch64` |
| Install type | Bare metal / VM / cloud / WSL2 (if applicable) |

## Hardware

| Field | Your value |
|-------|------------|
| GPU | `lspci` line or “CPU only” |
| NVIDIA | Output of `nvidia-smi` (if any) or “N/A” |
| RAM | Approximate GB |

## Docker

| Field | Your value |
|-------|------------|
| Docker version | `docker --version` |
| Compose | `docker compose version` or `docker-compose version` |
| Docker info | Does `docker info` work without sudo? (yes/no) |
| User in `docker` group? | (yes/no / unknown) |

## Structured preflight (required)

From the `ods` directory, run:

```bash
./scripts/linux-install-preflight.sh --json
```

Paste the **JSON output** (redact paths if needed). If the command fails, paste the **human** output:

```bash
./scripts/linux-install-preflight.sh
```

## Service preflight (after install)

If services are installed, also run:

```bash
./ods-preflight.sh
```

Paste the last 40 lines of output (or attach the log path printed at the end).

## Logs to attach (pick what applies)

- Installer log if referenced by the installer.
- `docker compose` error from the failing command (not the entire daemon log unless asked).
- For GPU issues: output of `docker info | grep -i nvidia` and relevant `nvidia-smi`.

## Privacy checklist

- [ ] Removed API keys and passwords from pasted content.
- [ ] Redacted internal hostnames if required by your employer.
- [ ] Confirmed no private URLs or tokens in compose overrides.

## Related docs

- [LINUX-TROUBLESHOOTING-GUIDE.md](LINUX-TROUBLESHOOTING-GUIDE.md) — maps check IDs to fixes.
- [INSTALL-TROUBLESHOOTING.md](INSTALL-TROUBLESHOOTING.md)
- [SUPPORT-MATRIX.md](SUPPORT-MATRIX.md)
