# Non-chat model download inventory

Reviewed on 2026-09-23 for PB-013 and PB-018. This inventories the core
installer's image, embedding and speech paths; it is not a license clearance or
an audit of every optional extension recipe and its transitive downloads.

| Path | Source and verification | Remaining boundary |
| --- | --- | --- |
| Image generation: [phase 11](../installers/phases/11-services.sh) | The [SDXL helper](../scripts/download-sdxl-model.py) downloads the exact revision below over HTTPS, bounds network time/size, checks size and SHA-256, and publishes from private staging without overwriting another file. An existing cache must pass the same checks. | Full checkpoint download and ComfyUI inference were not executed for this change. License/notice review remains separate. A rejected existing file is preserved for owner inspection; this helper does not stop other applications from opening it. |
| Offline memory embeddings: [phase 9](../installers/phases/09-offline.sh) | Nomic Embed Text v1.5 Q4_K_M currently resolves a mutable `main` reference and verifies nonempty GGUF magic only. | Pin the artifact and verify all bytes before publishing and reusing the cache. GGUF magic is not an integrity check. |
| Online embeddings: [TEI service](../extensions/services/embeddings/compose.yaml) | The container downloads the selected `EMBEDDING_MODEL` (default BAAI/bge-base-en-v1.5). | Container tag, model revision, transitive files and notice binding are not covered by the GGUF installer receipts. |
| Speech recognition: [Whisper service](../extensions/services/whisper/compose.yaml) and [phase 12](../installers/phases/12-health.sh) | Speaches receives a model-download request; the installer checks its model-list response before claiming availability. | Cache presence is not a per-file cryptographic receipt. The selected Whisper model and image need their own immutable revision/notice inventory. |
| Text to speech: [Kokoro service](../extensions/services/tts/compose.yaml) | Kokoro FastAPI supplies the voice runtime via a versioned image tag. | The image and its bundled or first-use model/voice files are outside the GGUF artifact gate; enumerate them before claiming complete coverage. |
| Optional snapshot helpers: [snapshot downloader](../scripts/download-hf-snapshot.py), [artifact fallback](../scripts/download-hf-artifact.py) and [pre-download command](../scripts/pre-download.sh) | These are acquisition helpers, not independent provenance authorities. The legacy snapshot path in pre-download refuses an unreviewed download. | A caller must bind revision, complete file list, hashes and terms before treating a snapshot as reviewed; successful transfer alone cannot provide that guarantee. |

## SDXL artifact evidence

The official Hugging Face metadata at revision
`c9a24f48e1c025556787b0c58dd67a091ece2e44` identifies:

- Publisher repository: `ByteDance/SDXL-Lightning`.
- File: `sdxl_lightning_4step.safetensors`.
- Length: `6938040682` bytes.
- LFS SHA-256: `e0d996ee0013e79d9d3561f50fcafb9a17e3ff07b780358e3b66d67932c4d490`.
- LFS pointer Git blob: `6768e72922448a3b705f8f4943275109263b603d`.

The [publisher card at that revision](https://huggingface.co/ByteDance/SDXL-Lightning/blob/c9a24f48e1c025556787b0c58dd67a091ece2e44/README.md)
identifies the full checkpoint as the ComfyUI variant and declares OpenRAIL++.
The [revision metadata](https://huggingface.co/api/models/ByteDance/SDXL-Lightning/revision/c9a24f48e1c025556787b0c58dd67a091ece2e44?blobs=true)
was fetched anonymously, without downloading the weights. The hash establishes
consistency with those publisher bytes; it does not establish trust, model
license clearance or a functioning installation.

## Validation

Ten inert-fixture tests passed on Windows Python 3.11 and Ubuntu/WSL Python
3.12, covering verified cache reuse, same-size corruption, truncated data,
network failure/timeout, symbolic links, spaces in paths, concurrent publication
and unsupported hard links. No test uses an environment override to disable
production integrity checks. A dedicated CI matrix runs these cases on Windows,
Linux and macOS; hosted results must be checked on the final candidate.

Linux/WSL installer lifecycle and local-image failure fixtures also passed.
The existing network-timeout suite passed 13 matching checks and explicitly
skipped 11 obsolete/nonmatching probes; this is not a complete network audit.
These fixtures do not constitute native clean-install or hardware qualification.
