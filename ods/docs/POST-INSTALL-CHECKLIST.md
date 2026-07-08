# ODS — Post-Install Checklist

Run these checks after installation to confirm everything is working.

---

## 1. Overall health

```bash
ods status
```

Shows container status, service health checks, and GPU metrics in one view. All enabled services should report **healthy**. If any show as not responding, check the logs (step 6 below).

## 2. LLM response test

```bash
ods chat "Hello, are you working?"
```

You should receive a text response within a few seconds. If you see an error, check `ods logs llm`.

## 3. Web interface

Open your browser and navigate to the address shown at the end of installation (default: `http://localhost:3000`). The Open WebUI chat interface should load and let you send a message.

## 4. GPU verification

**NVIDIA** — GPU utilisation, VRAM, and temperature appear automatically in `ods status`.

**AMD:**
```bash
rocm-smi
```

**Apple Silicon** — GPU is used automatically; no separate check needed.

## 5. Check enabled services

```bash
ods list
```

Core services (llama-server, open-webui, dashboard) should be shown as running. Optional services selected during install should also appear.

## 6. Diagnose a failing service

```bash
ods logs <service>     # e.g. ods logs llm
```

Replace `<service>` with the name from `ods list`. Common aliases: `llm` for llama-server, `stt` for Whisper, `tts` for Kokoro.

---

If a service fails its health check after reviewing logs, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
