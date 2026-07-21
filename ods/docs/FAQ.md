# ODS FAQ

Quick answers to common questions.

> **Looking for install/runtime troubleshooting?** See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) and [INSTALL-TROUBLESHOOTING.md](INSTALL-TROUBLESHOOTING.md).

---

## Hardware

### What hardware do I need?

**Lightweight (runs on anything):**
- GPU: Any (or CPU-only)
- RAM: 4GB+
- Storage: 15GB free
- Model: Qwen3.5 2B (auto-selected)

**Minimum (comfortable):**
- GPU: RTX 3060 12GB or RTX 4060 8GB
- RAM: 32GB
- Storage: 500GB NVMe SSD
- CPU: Any modern quad-core

**Recommended (comfortable daily use):**
- GPU: RTX 4070 Ti Super 16GB or RTX 4090 24GB
- RAM: 64GB
- Storage: 1TB NVMe SSD

**Why these specs?**
- 12GB VRAM = 7B-14B models, basic tasks
- 16GB VRAM = 32B models with reduced context
- 24GB VRAM = 32B models with full context, voice pipeline
- 48GB+ VRAM (2x 4090) = Multiple models, concurrent users

### How much does a build cost?

| Tier | GPU | Total Build | What You Get |
|------|-----|-------------|--------------|
| Entry | RTX 3060 12GB | $800-1,200 | Basic chat, slow but works |
| Prosumer | RTX 4070 Ti 16GB | $2,000-3,000 | Comfortable single-user |
| Pro | RTX 4090 24GB | $4,000-6,000 | Fast, voice agents, 5-10 users |
| Enterprise | 2x RTX 4090 | $12,000-18,000 | 20-40 concurrent users |

See [HARDWARE-GUIDE.md](HARDWARE-GUIDE.md) for full breakdown.

### What about electricity costs?

- Idle: 50-100W (~$5-15/month)
- Active inference: 300-450W per GPU
- 24/7 heavy use: $30-80/month depending on rates

Still cheaper than cloud API bills at moderate usage.

---

## Capabilities

### What can ODS do?

**Out of the box:**
- 💬 ChatGPT-style web interface (Open WebUI)
- 🎤 Voice transcription (Whisper)
- 🔊 Text-to-speech (Kokoro)
- 📄 Document Q&A with RAG (Qdrant + embeddings)
- 🔗 API integration (OpenAI-compatible endpoints)
- 🤖 Agent workflows (n8n)

**With voice profile:**
- 🎙️ Full voice agents (speak in, speak out)
- Real-time conversations at <2s latency

**With optional components:**
- 🔒 Privacy Shield (PII redaction proxy)
- 🖼️ Image generation (SDXL Lightning via ComfyUI)
- 🔍 Local web search (SearXNG)

### How fast is it?

**Real benchmarks from our dual-4090 cluster:**

| Scenario | Latency | Concurrent Users |
|----------|---------|------------------|
| Single chat request | ~1.4s | 1 |
| 10 simultaneous chats | ~1.5s | 10 |
| 20 simultaneous chats | ~1.6s | 20 |
| Voice agent (full round-trip) | <2s | 15-20 per GPU |

Your results depend on hardware tier. Single 4090 ≈ half the concurrent capacity.

### Is it as good as GPT-4 / Claude?

**Honest answer:** For most tasks, 32B local models are 80-90% as capable.

**Where local wins:**
- Speed (no network latency)
- Privacy (data never leaves your network)
- Cost (no per-token fees)
- Control (choose your model, tune prompts, no content filters)

**Where cloud wins:**
- Cutting-edge reasoning (GPT-4, Claude 3.5)
- Multimodal (vision, though Qwen-VL is catching up)
- Zero maintenance

**Our recommendation:** Use local for daily work, cloud for edge cases.

---

## Cost & ROI

### How does cost compare to cloud APIs?

**Example: 100,000 tokens/day usage**

| Option | Monthly Cost | Notes |
|--------|--------------|-------|
| OpenAI GPT-4 | ~$300-600 | Per-token billing |
| Claude API | ~$200-400 | Per-token billing |
| ODS | $30-80 | Electricity only (after hardware) |

**Break-even timeline:**
- Light use (~$50/mo API): 2-3 years
- Medium use (~$200/mo API): 6-12 months
- Heavy use (~$500+/mo API): 3-6 months

Plus: No usage caps, no rate limits, no surprise bills.

### What about maintenance costs?

**Time investment:**
- Initial setup: 1-2 hours with install wizard
- Ongoing maintenance: ~30 min/month (updates, monitoring)
- Model updates: Optional, 1-click when you want them

**No paid support required** for most users. Community Discord available.

---

## Privacy & Security

### Is it really private?

**Yes, 100%.** Your prompts never leave your local network.

- No data sent to cloud providers
- No logging by third parties
- No training data contribution
- Full GDPR/HIPAA compliance capability

### Can I use it with sensitive data?

Yes. Common use cases:
- Legal document review
- Medical record analysis
- Financial data processing
- Internal company communications
- Client confidential work

**Optional:** Add Privacy Shield for automatic PII redaction as an extra layer.

### What about model security?

- Models run in Docker containers (isolated)
- No outbound network required after initial download
- You control which models to run
- Can air-gap the server if needed

---

## Setup & Support

### How hard is it to set up?

**With install wizard:** Under 1 hour for someone comfortable with terminal.

```bash
curl -fsSL https://install.osmantic.com/ods.sh | bash
```

The hosted endpoint proxies the current bootstrap from repository `main`.
Reviewed merges reach it automatically after edge-cache refresh. `ODS_REF` selects a compatible repository checkout. See
[Installer Trust](INSTALLER_TRUST.md) to inspect the script or install a stable
release or audited commit manually.

The wizard:
1. Detects your hardware
2. Recommends configuration
3. Downloads models
4. Starts services
5. Runs health checks

### What if I'm not technical?

Options:
1. **Pre-configured hardware:** We can ship ready-to-plug-in units
2. **Remote setup service:** $200-500 depending on complexity
3. **Detailed guides:** Step-by-step docs for common scenarios

### How do I get updates?

```bash
ods update
```

Updates are optional — you control when to apply them.

**Preview changes without applying:**
```bash
ods update --dry-run
```

**Skip version-compatibility confirmation:**
```bash
ods update --force
```

`ods update` automatically creates a pre-update snapshot before pulling new images, then verifies all services are healthy afterward. If something goes wrong, run:

```bash
ods rollback
```

This restores configuration from the pre-update snapshot and restarts services.

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

`ods update` always creates a pre-update snapshot, so `ods rollback` is available immediately after any update attempt.

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

### Can I chat while models are downloading?

Yes. During install, a small bootstrap model (~1.5GB, Qwen 3.5 2B) downloads first so you can start chatting within a couple of minutes. The bootstrap context is 64K so Hermes can work during the first session. The full tier-appropriate model downloads in the background.

When the full model finishes, the system swaps it in automatically — you don't need to do anything. `ods status` shows the current bootstrap state if a swap is still in progress.

---

### Where do I get help?

1. This documentation
2. `TROUBLESHOOTING.md` for common issues
3. GitHub Issues: https://github.com/Osmantic/ODS/issues
4. Discord community (link in README)

---

## Comparisons

### ODS vs Ollama?

| Feature | ODS | Ollama |
|---------|--------------|--------|
| Web UI | ✅ Built-in (Open WebUI) | ❌ Separate install |
| Voice | ✅ Full pipeline | ❌ Not included |
| RAG | ✅ Built-in | ❌ Not included |
| n8n workflows | ✅ Included | ❌ Not included |
| One-command setup | ✅ Yes | ⚠️ Partial |
| Performance | ✅ llama-server (faster) | ⚠️ Ollama |

**Ollama is great for quick experiments.** ODS is a complete production stack.

### ODS vs LocalAI?

LocalAI is developer-focused. ODS is user-focused.

- LocalAI: More flexibility, more configuration needed
- ODS: Opinionated defaults, works out of box

### ODS vs cloud APIs?

See "Cost & ROI" section above. TL;DR: Local is cheaper at scale, more private, but requires hardware investment.

---

*Built by Osmantic / The Collective*
