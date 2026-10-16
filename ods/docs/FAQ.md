# ODS FAQ

> **Release channel:** the install commands on this page fetch development `main`, which is not signed. A signed-source path is staged in [Verified Install Preview](VERIFIED_INSTALL_PREVIEW.md); it is not active until the first eligible immutable release is published, and historical `v3.0.0` is not eligible.

Quick answers to common questions.

> **Looking for install/runtime troubleshooting?** See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and [INSTALL-TROUBLESHOOTING.md](INSTALL-TROUBLESHOOTING.md).

---

## Hardware

### What hardware do I need?

ODS picks a model for the memory it detects; the
[hardware table in the README](../../README.md#hardware-auto-detection) shows
the current picks. Roughly:

- CPU only: 8-12 GB of RAM runs Qwen3.5 2B, 16-20 GB runs Qwen3.5 4B, 24 GB or
  more runs Qwen3.5 9B.
- NVIDIA 8-16 GB of VRAM runs Qwen3.5 9B (64K context); 24-32 GB runs Qwen3.5
  27B (64K).
- NVIDIA 40 GB or more, AMD Strix Halo with 64-128 GB, or Apple Silicon with
  48 GB or more runs Qwen3.6 35B-A3B (128K); NVIDIA 90 GB or more on x86_64 runs
  Qwen3 Coder Next (128K).

The installer needs 30 GB (most machines) to 150 GB (40 GB GPUs) of free disk
plus room for the model, and NVIDIA needs driver 570 or newer. See
[Hardware Sizing](HARDWARE-GUIDE.md) for the per-tier disk and RAM checks.

### How fast is it, and how many users can it serve?

ODS publishes no measured price, throughput or capacity figures. A default
install serves one request at a time; see
[Multi-User Setup](MULTI-USER-SETUP.md) and run `ods benchmark` on your own
hardware.

---

## Capabilities

### What can ODS do?

**Out of the box:**
- 💬 Dashboard/Portal agent chat on qualified Linux hosts and Apple Silicon Macs; Open WebUI remains the chat fallback on other hosts
- 🔗 API integration (OpenAI-compatible endpoints)

Whisper, Kokoro, RAG, n8n, and Open WebUI on qualified Linux hosts can be added when needed. Existing installations retain their selected services. Use `--with-webui` during a Linux install or add it later from the Extensions Library.

**With voice profile:**
- 🎙️ Voice conversations (speak in, speak out) with ODS Talk

**With optional components:**
- 🔒 Privacy Shield (PII redaction proxy)
- 🖼️ Image generation (SDXL Lightning via ComfyUI)
- 🔍 Local web search (SearXNG)

### Is it as good as a frontier cloud model?

Local models are smaller than the largest cloud models, so expect weaker
results on the hardest reasoning tasks. In exchange, inference stays on your
hardware, there are no per-token fees, and you choose the model. You can route
specific requests to a cloud provider when you want one (`ods mode hybrid`).

---

## Privacy & Security

### Is it really private?

Inference and chat history stay on your machine, and ODS collects no
telemetry. Unless you configure a cloud or remote provider, prompts are not
sent to model providers. By default ODS reaches the internet only to download
models and images, to check GitHub for ODS releases, and to run web searches
the Portal agent makes for you through its search provider. See
[Is my data private?](../FAQ.md#is-my-data-private) for how to turn each off.

- No data sent to cloud model providers unless you configure one
- No usage telemetry from ODS or its bundled services
- No training data contribution
- Running locally can support GDPR/HIPAA programs; compliance depends on how you
  deploy and operate it

### Can I use it with sensitive data?

Yes. Common use cases:
- Legal document review
- Medical record analysis
- Financial data processing
- Internal company communications
- Client confidential work

**Optional:** Add Privacy Shield for automatic PII redaction as an extra layer.

### What about model security?

- The model server runs in a Docker container on Linux; on macOS llama-server
  runs natively, and on Windows AMD hosts `llama-server.exe` runs natively on
  Windows.
- Inference needs no outbound network after the initial download.
- You control which models to run.
- The server can be air-gapped if needed.

---

## Setup & Support

### How hard is it to set up?

One command on a supported system; the installer detects your hardware,
downloads a model and starts the services.

Linux/macOS:

```bash
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

The hosted endpoint proxies the current bootstrap from repository `main`.
Reviewed merges reach it automatically after edge-cache refresh. `ODS_REF` selects a compatible repository checkout. See
[Installer Trust](INSTALLER_TRUST.md) to inspect the script or install an
audited commit manually.

Windows:

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

Do not run the `curl ... | bash` installer from Windows PowerShell. The Windows entry point guides Ubuntu/WSL2 preparation and requires Pixel with Hermes disabled. It installs missing WSL, Docker Desktop and Ubuntu after asking, continues by itself after the one restart, and opens Portal when done; see [Windows Quickstart](WINDOWS-QUICKSTART.md).

The wizard:
1. Detects your hardware
2. Recommends configuration
3. Downloads models
4. Starts services
5. Runs health checks

### How do I get updates?

`ods update` refreshes the container images pinned by your installed version
and recreates the containers:

```bash
ods update            # apply
ods update --dry-run  # preview
```

It first tries to take a snapshot (a failure is reported, not fatal) and
afterwards checks that the services are running; `ods rollback` restores the
snapshot. It does not change ODS code. Code changes, including security fixes,
arrive only by re-running a newer installer; see
[Updating an existing installation](../SECURITY.md#updating-an-existing-installation).

---

### How do I back up and restore my data?

**Create a backup** (saves user data and config to `.backups/`):
```bash
ods backup
```

**Create a compressed backup:**
```bash
ods backup -c
```

**List existing backups:**
```bash
ods backup -l
```

**Verify a backup's integrity:**
```bash
ods backup verify <backup_id>
```

**Restore from a backup** (interactive — lets you choose from available backups):
```bash
ods restore
```

**Restore a specific backup by ID:**
```bash
ods restore <backup_id>
```

**Rollback after a failed update** (restores the pre-update snapshot):
```bash
ods rollback
```

`ods update` normally creates a pre-update snapshot first, so `ods rollback` is
usually available right after an update. On Portal (Pixel) installs, ordinary
backups refuse to run because they can't capture Portal's state;
`ods backup -t config` still works.

---

### What are service templates?

Templates are curated presets that enable a group of extensions suited to a specific use case — for example, a creative-studio setup (image generation + voice) or a research workflow (RAG + web search + agents).

**List available templates:**
```bash
ods template list
```

**Preview what a template will change before applying:**
```bash
ods template preview <template-id>
```

**Apply a template (enables the template's services):**
```bash
ods template apply <template-id>
```

Applying a template only enables services — it doesn't disable anything you've already set up.

---

### Can ODS reuse a model already running in Ollama or LM Studio?

The Linux installer can reuse a host-managed text/chat model instead of
downloading and starting a duplicate GGUF with ODS's llama-server.

Interactive installs discover matching local services and ask before adopting
one. Non-interactive installs require an explicit decision:

```bash
./install.sh --reuse-external-llm --non-interactive
```

Or configure the endpoint and exact provider model directly:

```bash
./install.sh \
  --external-llm-url http://127.0.0.1:11434 \
  --external-llm-provider ollama \
  --external-llm-model qwen3.5:9b
```

The installer verifies the model and a real completion before changing the
Compose topology. Open WebUI, ODS Talk, Hermes, Perplexica, Privacy Shield, and
Token Spy then use the container-safe external endpoint. ODS does not stop the
external process and does not activate local catalog models while that backend
is selected.

To return an existing installation to ODS-managed llama-server:

```bash
./install.sh --no-external-llm
```

For another OpenAI-compatible server, such as a Lemonade Server you run
yourself, use `--external-llm-provider openai-compatible` with the server's URL
and exact model id.

This integration routes text/chat inference; it does not import or synchronize
Ollama/LM Studio model files, VLMs, embedding models, or rerankers. The
installer flags in this release are Linux-only. The Windows AMD
`llama-server.exe` and macOS native llama-server keep their existing platform
lifecycle.

---

### Can I chat while models are downloading?

Yes. The installer first downloads a small bootstrap model (Qwen3.5 2B, about
1.3 GB) and starts it with a 64K context, then downloads your full model in the
background. When the download is verified, ODS restarts the model server on the
full model; requests in flight during that restart need a retry. `ods status`
shows the bootstrap state while a switch is pending.

---

### Where do I get help?

1. This documentation and [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
2. Questions: [GitHub Discussions](https://github.com/Osmantic/ODS/discussions)
3. Bugs: [GitHub Issues](https://github.com/Osmantic/ODS/issues)
4. Security problems: report them privately through
   [Security → Report a vulnerability](https://github.com/Osmantic/ODS/security/advisories/new)

---

## Comparisons

See [How It Compares](../../README.md#how-it-compares) in the README.

---

*Built by Osmantic / The Collective*
