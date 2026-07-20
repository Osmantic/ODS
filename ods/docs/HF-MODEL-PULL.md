# Pulling Models from Hugging Face

The dashboard's Models page can pull models directly from any Hugging Face
repo — not just the fixed entries in `config/model-library.json` — and land
each file where its backend actually looks for it.

## Using It

Click **Pull from Hugging Face** in the Models page header:

1. **Search** — searches the HF Hub. Gated repos show a `gated` badge.
2. **Pick files** — lists the repo's `.gguf`/`.safetensors` files with sizes.
   Sharded models (`<name>-00001-of-00005.gguf`) are collapsed into one
   selectable item covering every shard. Select one or more items.
3. **Confirm** — each selected item gets a destination, auto-suggested from
   its name/path (`.gguf` → llama-server; `vae` → ComfyUI's `vae/`; `lora`,
   `controlnet`, `clip`/`t5`, upscalers, embeddings → their matching
   ComfyUI dirs; anything else → `checkpoints/`). Override any suggestion
   with the dropdown, then pull.

Progress appears in the same download bar as catalog downloads, with a
Cancel button. GGUF pulls show up in the model table on the next refresh
(directory scan); ComfyUI files are visible to ComfyUI immediately
(bind-mounted dir — just refresh the model dropdown in its UI).

## Gated / Private Repos

Click **Connect account** (or the link under the search box) and paste a
Hugging Face access token (`hf_...`, a fine-grained read token is enough).
The token is stored server-side in `.env` as `HF_TOKEN` — the browser never
sees it again, only a connected/not-connected state — and is attached to HF
API calls and downloads from then on. Public repos work without a token.

## Where Files Land

| Destination | Directory |
|---|---|
| llama-server (GGUF) | `data/models/` (flat) |
| ComfyUI | `data/comfyui/ComfyUI/models/<type>/` |

One download runs at a time, system-wide (shared with catalog downloads).
Files already present are not re-downloaded (and are never checksum-deleted
by a later pull that happens to ship a same-named file). A disk-space
preflight rejects pulls that don't fit, using HEAD Content-Length with the
HF listing's size as fallback.

Every completed pull writes a receipt to `data/hf-pulls/` recording exactly
which files landed where (`repo_id`, `revision`, per-file target and path).
Nothing consumes these yet; they exist so a future "remove this pull" action
can delete precisely what a pull placed instead of guessing by filename.

## What It Deliberately Does Not Do

- **hipfire models** — hipfire pulls and quantizes its own weights on
  activation (see `extensions/services/hipfire/`); a raw download can't
  feed it. Its catalog entry is managed via model activation, not this flow.
- **Diffusers-format pipelines** — repos laid out as `unet/` + `vae/` +
  `text_encoder/` folders driven by `model_index.json` can have individual
  files pulled (nested paths are supported), but ODS does not reassemble
  the pipeline; ComfyUI consumes single-file checkpoints/components, not
  diffusers trees.

## Plumbing (for developers)

Dashboard → `dashboard-api` (`routers/models.py`: `/api/models/hf/search`,
`/api/models/hf/files`, `/api/models/hf/auth`, `/api/models/pull`) → host
agent (`/v1/model/pull-hf`, documented in [HOST-AGENT-API.md](HOST-AGENT-API.md)),
which does the actual curl download with resume/retry/cancel and progress
via `data/model-download-status.json`. Token saves go through the host
agent's `/v1/env/set-keys` (dashboard-api's `.env` mount is read-only and
its view goes stale after any env write — see the endpoint doc for why
whole-file writes from the container are unsafe). Input validation runs
independently at both layers; the host agent never trusts dashboard-api's.
