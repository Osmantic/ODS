#!/usr/bin/env python3
"""Generate ``data/persona/SOUL.md`` from the static persona template plus a
detected snapshot of *this* Dream Server install — what GPU backend it has,
which model is loaded, which services are running, what URLs are reachable.

The goal is for Dream (the agent) to answer accurately when the operator
asks "what can you do here?" or "is comfyui running?" or "what model are you
using?" — instead of inventing capabilities it doesn't have, or denying
ones it does. The dynamic context block is bounded to a few hundred tokens
so it doesn't bloat Hermes's 16k-token system prompt.

Idempotent. Safe to re-run after enabling/disabling services. Designed to
be invoked from:

  * installer phase 11 (after services come up — so docker ps reports the
    real state, not the pre-launch one)
  * ``dream restart hermes`` (so the persona reflects current state on every
    container recreate)
  * the operator manually when they want to refresh

Reads only — never modifies SOUL.md.template, never touches anything other
than the output path.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
from pathlib import Path

# Service-id → (display name, one-line "what it does and how the user
# reaches it"). Tuned so the model gets actionable, specific info — port
# numbers + intended use — not just service names. The agent itself often
# can't directly call these (its tools are web_search / files / code /
# memory / etc.), but it can point the user at the right surface.
_SERVICE_CAPABILITIES: dict[str, tuple[str, str]] = {
    "llama-server": ("Local LLM inference", "the engine running my chat model (port 8080 internal)"),
    "open-webui": ("Open WebUI", "a desktop-style chat surface, reachable at chat.{device}.local"),
    "dashboard": ("Dream dashboard", "the operator's local web UI for managing services, models, and extensions — `{device}.local`"),
    "hermes": ("Hermes Agent", "that's me — the agent runtime, port 9119 internal"),
    "searxng": ("SearXNG", "privacy-respecting metasearch the operator can hit at port 8888; this is what my `web_search` tool uses under the hood"),
    "brave-search": ("Brave Search backend", "alternative paid web-search backend (when an API key is set)"),
    "perplexica": ("Perplexica", "web-augmented Q&A UI on top of SearXNG"),
    "comfyui": ("ComfyUI", "image + video generation (SDXL Lightning, LTX-2.3). UI on port 8188. The operator can ask me to suggest a prompt for it but I don't generate images directly — I'd point them at ComfyUI"),
    "tts": ("Kokoro TTS", "text-to-speech (port 8880). My replies in Dream Talk can be spoken aloud through this"),
    "whisper": ("Whisper STT", "speech-to-text (port 8000). Used for voice messages in Dream Talk"),
    "embeddings": ("Embeddings server", "vector embeddings (port 8090) — feeds the RAG path"),
    "qdrant": ("Qdrant", "vector database (port 6333) for semantic recall and RAG"),
    "n8n": ("n8n", "visual no-code workflow + automation engine on port 5678. Operators wire scheduled jobs and integrations here"),
    "ape": ("APE", "agentic prompt engineering surface"),
    "opencode": ("OpenCode", "coding agent (an alternative to me for code work)"),
    "openclaw": ("OpenClaw", "older Claude-style agent (being deprecated in favor of me)"),
    "privacy-shield": ("Privacy Shield", "PII scrubber that can sit in front of LLM calls"),
    "token-spy": ("Token Spy", "inference traffic introspection"),
    "tailscale": ("Tailscale", "mesh VPN — the operator can reach this whole stack remotely without exposing ports to the public internet"),
    "langfuse": ("Langfuse", "LLM call tracing + analytics (port 3010)"),
}

# Where the dynamic block gets inserted in SOUL.md.template. The template
# has the literal marker line; this script replaces it.
_INSERT_MARKER = "<!-- INSTALLATION_CONTEXT -->"


def _read_env(env_path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Ignores comments / blank lines / quotes."""
    out: dict[str, str] = {}
    if not env_path.exists():
        return out
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        v = v.strip()
        # Strip surrounding quotes if present
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def _container_to_service_map(repo_root: Path) -> dict[str, str]:
    """Build a {container_name: service_id} index from extension manifests.

    Manifests declare both ``service.id`` and an explicit ``container_name``
    (often ``dream-<id>`` but sometimes different, e.g. open-webui →
    dream-webui). We need both directions: incoming docker ps names are
    container names, the capability mapping below keys on service-id.

    Cheap regex over manifest.yaml — avoiding a YAML dep keeps this script
    runnable with Python's stdlib only.
    """
    index: dict[str, str] = {}
    services_dir = repo_root / "extensions" / "services"
    if not services_dir.is_dir():
        return index
    id_re = re.compile(r"^\s*id:\s*([A-Za-z0-9_-]+)\s*(?:#.*)?$")
    cname_re = re.compile(r"^\s*container_name:\s*([A-Za-z0-9_-]+)\s*(?:#.*)?$")
    for manifest in sorted(services_dir.glob("*/manifest.yaml")):
        sid: str | None = None
        cnames: list[str] = []
        in_service = False
        for raw in manifest.read_text(encoding="utf-8").splitlines():
            if raw.startswith("service:"):
                in_service = True
                continue
            if in_service and not raw.startswith((" ", "\t")) and raw.strip():
                in_service = False
            if not in_service:
                continue
            m = id_re.match(raw)
            if m and sid is None:
                sid = m.group(1)
            m = cname_re.match(raw)
            if m:
                cnames.append(m.group(1))
        if sid:
            # Always include the default `dream-<id>` convention too —
            # some manifests omit container_name when it matches.
            cnames.append(f"dream-{sid}")
            for cname in cnames:
                index[cname] = sid
    return index


def _running_services(repo_root: Path) -> set[str]:
    """Set of service-ids currently up per ``docker ps``. Cross-references
    container names against the manifest-built {container_name: service_id}
    map so e.g. ``dream-webui`` resolves to service id ``open-webui``.
    Children of compound services (``dream-langfuse-clickhouse`` etc.) get
    collapsed to their parent service-id via prefix match. Containers that
    don't match any manifest are skipped — they're either non-Dream sidecars
    or post-install user additions we shouldn't claim as ours.
    """
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}", "--filter", "name=dream-"],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return set()

    cmap = _container_to_service_map(repo_root)
    known_sids = set(cmap.values())
    running: set[str] = set()
    for line in result.stdout.splitlines():
        cname = line.strip()
        if not cname:
            continue
        # Exact manifest match first
        if cname in cmap:
            running.add(cmap[cname])
            continue
        # Compound-service collapse: dream-langfuse-clickhouse → "langfuse"
        # if the latter exists as a known service id.
        if cname.startswith("dream-"):
            stem = cname[len("dream-"):]
            parts = stem.split("-")
            for i in range(len(parts), 0, -1):
                candidate = "-".join(parts[:i])
                if candidate in known_sids:
                    running.add(candidate)
                    break
    return running


def _loaded_model(llm_port: int = 8080) -> str | None:
    """Best-effort: ask llama-server / Lemonade what's currently loaded.
    Returns the model id, or None on any failure (network, no service)."""
    # Try Lemonade health first — has structured per-model state
    for path in ("/api/v1/health", "/v1/models"):
        try:
            import urllib.request
            with urllib.request.urlopen(f"http://127.0.0.1:{llm_port}{path}", timeout=3) as resp:
                data = json.load(resp)
        except Exception:
            continue
        loaded = data.get("all_models_loaded") if isinstance(data, dict) else None
        if isinstance(loaded, list) and loaded:
            return loaded[0].get("model_name")
        models = data.get("data") if isinstance(data, dict) else None
        if isinstance(models, list) and models:
            return models[0].get("id")
    return None


def _humanize_gpu(env: dict[str, str]) -> str:
    """One-line GPU/backend description from .env."""
    backend = (env.get("GPU_BACKEND") or "cpu").lower()
    mode = env.get("DREAM_MODE", "").lower()
    if backend == "amd":
        if mode == "lemonade":
            return "AMD GPU (ROCm/Vulkan via Lemonade)"
        return "AMD GPU"
    if backend == "nvidia":
        return "NVIDIA GPU (CUDA via llama.cpp)"
    if backend == "apple":
        return "Apple Silicon (Metal via llama.cpp)"
    if backend == "cpu":
        return "CPU-only (no GPU acceleration)"
    return backend


def _capabilities_block(running: set[str], device: str) -> list[str]:
    """Render the user-facing 'what's installed and reachable' bullets.
    Only lists services that are *actually running* — not just defined.
    Substitutes ``{device}`` into descriptions so the agent gets the live
    LAN hostname instead of a placeholder."""
    bullets: list[str] = []
    seen = set()
    # Display order: search → vision/voice → storage → automation → other.
    priority = [
        "searxng", "perplexica", "brave-search",
        "comfyui",
        "tts", "whisper",
        "qdrant", "embeddings",
        "n8n",
        "ape", "opencode", "privacy-shield", "token-spy",
        "langfuse",
        "open-webui",
        "tailscale",
    ]
    for sid in priority:
        if sid in running and sid in _SERVICE_CAPABILITIES:
            label, desc = _SERVICE_CAPABILITIES[sid]
            bullets.append(f"- **{label}** — {desc.replace('{device}', device)}")
            seen.add(sid)
    # Anything running we didn't enumerate gets a generic line so future
    # services don't silently vanish from the agent's knowledge.
    for sid in sorted(running - seen):
        if sid in {"llama-server", "hermes", "hermes-proxy", "dashboard",
                   "dashboard-api", "dream-proxy", "litellm"}:
            continue  # internal plumbing — agent doesn't need to mention these
        if sid in _SERVICE_CAPABILITIES:
            label, desc = _SERVICE_CAPABILITIES[sid]
            bullets.append(f"- **{label}** — {desc.replace('{device}', device)}")
        else:
            bullets.append(f"- **{sid}** — service running on this host")
    return bullets


def build_context_block(env_path: Path) -> str:
    """Render the dynamic 'About this installation' Markdown block."""
    env = _read_env(env_path)
    repo_root = env_path.resolve().parent
    running = _running_services(repo_root)

    device = env.get("DREAM_DEVICE_NAME") or socket.gethostname() or "this machine"
    gpu = _humanize_gpu(env)
    model_hint = env.get("LLM_MODEL") or env.get("GGUF_FILE") or "the locally-served model"
    live_model = _loaded_model()
    if live_model:
        model_hint = live_model

    ctx_size = env.get("CTX_SIZE") or env.get("MAX_CONTEXT") or "?"
    if ctx_size.isdigit() and int(ctx_size) >= 1024:
        ctx_pretty = f"{int(ctx_size) // 1024}k"
    else:
        ctx_pretty = ctx_size

    talk_host = f"talk.{device}.local"
    dash_host = f"{device}.local"
    chat_host = f"chat.{device}.local"

    bullets = _capabilities_block(running, device)

    # Lead with the install identity — hard factual claims phrased so the
    # model doesn't drift into running tool calls to "verify." Hermes's
    # own system prompt advertises a Hermes dashboard at port 9119; the
    # model has a strong prior to mention that when asked about
    # "dashboard," which is wrong on a Dream Server install. We explicitly
    # distinguish below.
    lines: list[str] = [
        "## About this Dream Server install — read this BEFORE answering questions about your environment",
        "",
        "**You are running inside Dream Server, a fully-local AI stack the operator installed on their own hardware.** Hermes Agent (what you run on) is one component of that stack — not the whole thing. When the operator asks about \"the dashboard,\" \"the system,\" \"what's running,\" \"what you can do,\" or \"your hardware,\" they mean the Dream Server install around you, not Hermes Agent in isolation.",
        "",
        "**These facts are authoritative. Don't run tool calls (terminal, file probes, GPU queries) to second-guess them — they are auto-generated from this exact install and refreshed when services change. Quote them directly:**",
        "",
        f"- **Host**: `{device}` (LAN hostname `{dash_host}`)",
        f"- **GPU / backend**: {gpu}",
        f"- **Chat model serving you right now**: `{model_hint}` (context window: {ctx_pretty})",
        f"- **Dream Server admin dashboard** (model management, extensions catalog, system status): `http://{dash_host}` — this is what the operator means by \"the dashboard.\" NOT Hermes's internal `:9119` endpoint, which is just where you live.",
        f"- **Mobile chat portal (Dream Talk)**: `http://{talk_host}` — the operator scans an owner-card QR code to land here on their phone",
        f"- **Open WebUI** (desktop-style chat surface, separate from Dream Talk): `http://{chat_host}`",
        "",
        "## Services running on this install right now",
        "",
        "Dream Server has an **extensions system** — services bundled with the stack that the operator can enable/disable from the dashboard's Extensions page without reinstalling. The list below is what's currently running. Some are tools you can call directly through your own tool-calling layer; others are surfaces you can point the operator at.",
        "",
        "**When asked verbally about this list, summarize it conversationally — don't recite the bullets.** A good answer is one or two sentences that names a few of the most relevant services for the question, not the whole catalog. Example: *\"Web search through SearXNG, image generation in ComfyUI, voice via Kokoro and Whisper, plus workflows in n8n — and there's more on the dashboard.\"* The bullets below are your reference; speak them as natural sentences.",
        "",
    ]
    if bullets:
        lines.extend(bullets)
    else:
        lines.append("- (no extension services running — only the core chat path is up)")

    lines.extend([
        "",
        "## Your direct capabilities vs. what the operator reaches separately",
        "",
        "**You can directly invoke these** via your tool-calling layer:",
        "- `web_search` — backed by the SearXNG service above (privacy-respecting, no rate limits)",
        "- `web_extract` — fetch a specific URL the operator names",
        "- `read_file` / `write_file` / `search_files` — in your sandboxed agent workspace",
        "- `execute_code` — sandboxed Python",
        "- `memory` — save / recall facts about the operator across sessions",
        "- `text_to_speech` — your replies on Dream Talk can be spoken aloud",
        "- A `skills` catalog with dozens of pluggable skill modules for specific domains (github, notion, kanban, research, etc.)",
        "",
        "**You can't directly invoke these but can refer the operator to them** when they ask:",
        "- **Image / video generation** → ComfyUI (if running above). The operator opens it from the Dream Server dashboard. You can help by suggesting prompts, but you don't generate the image yourself.",
        "- **Automated workflows / scheduled jobs / third-party integrations** → n8n (if running above). For simple recurring agent tasks you have your own `cronjob` tool.",
        "- **Enable more extensions** → Extensions page on the Dream Server dashboard (`http://" + dash_host + "/extensions`). Catalog includes Aider, OpenCode, Privacy Shield, Tailscale, Perplexica, etc.",
        "- **Swap or download a different LLM** → Models page on the Dream Server dashboard. Operators can pull from Hugging Face directly.",
        "- **Remote access from outside the LAN** → Tailscale (if running above)",
        "",
        "When the operator names a service you don't see in the list above, say so honestly — \"that's not running on this install\" — rather than invent it. The list reflects what `docker ps` reports as of the last regeneration.",
    ])
    return "\n".join(lines)


def build_soul(template_path: Path, env_path: Path, output_path: Path) -> bool:
    """Render the assembled SOUL.md. Returns True if the file actually
    changed (so callers can decide whether to bounce Hermes)."""
    template = template_path.read_text(encoding="utf-8")
    context = build_context_block(env_path)

    if _INSERT_MARKER in template:
        assembled = template.replace(_INSERT_MARKER, context)
    else:
        # Template doesn't have the marker yet — append the block so
        # operators upgrading from an older template still get the
        # installation-context behaviour.
        assembled = template.rstrip() + "\n\n" + context + "\n"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Self-heal a pathological state: Docker's bind-mount engine auto-creates
    # the source path as a *directory* when the path doesn't exist at
    # compose-up time. If a previous install ran compose-up before this
    # script generated the file, ``data/persona/SOUL.md`` is now an empty
    # directory, and the next compose-up keeps failing with "not a directory:
    # Are you trying to mount a directory onto a file." Remove that directory
    # so we can write the real file in its place. Caught when re-running
    # /dream-fleet-test on mac-mini after a prior failed install.
    if output_path.exists() and not output_path.is_file():
        import shutil
        shutil.rmtree(output_path)
    previous = output_path.read_text(encoding="utf-8") if output_path.is_file() else ""
    if previous == assembled:
        return False
    output_path.write_text(assembled, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Build data/persona/SOUL.md from the static persona + detected install state.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=repo_root / "extensions" / "services" / "hermes" / "SOUL.md.template",
    )
    parser.add_argument(
        "--env",
        type=Path,
        default=repo_root / ".env",
    )
    parser.add_argument(
        "--output",
        type=Path,
        # Keep this on the host-user side of the data directory tree —
        # data/hermes/ is owned by uid 10000 (HERMES_UID) so the host's
        # operator can't write into it. data/persona/ is a separate
        # operator-owned directory bind-mounted ro into the Hermes
        # container at /opt/hermes/docker/SOUL.md.
        default=repo_root / "data" / "persona" / "SOUL.md",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Print the assembled file to stdout instead of writing it.",
    )
    args = parser.parse_args(argv)

    if args.check:
        ctx = build_context_block(args.env)
        sys.stdout.write(ctx)
        sys.stdout.write("\n")
        return 0

    if not args.template.exists():
        print(f"ERROR: template not found at {args.template}", file=sys.stderr)
        return 2

    changed = build_soul(args.template, args.env, args.output)
    print("changed" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
