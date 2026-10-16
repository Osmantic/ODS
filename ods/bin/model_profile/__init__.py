"""Model profile: what one GGUF can do on this machine with this llama.cpp build.

A host-owned, measured record (PLAN WP3). ``probes`` holds the capability
battery the host agent runs once per model, runtime build and host, inside
the first switch; ``store`` persists results in ``data/model-profiles.json``.

Stdlib only: the standalone host agent imports this from the installed tree.
"""

# 2: always-thinking models get at least 1024 tokens per probe (2026-10-09).
# 3: a streamed probe answer may be 2 MiB, so long streams are measured.
SUITE_VERSION = "3"
