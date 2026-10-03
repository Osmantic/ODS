import os
import platform
import re
import sys
from pathlib import Path

if platform.machine().lower() in {"x86_64", "amd64"}:
    required = {"cx16", "lahf_lm", "popcnt", "ssse3", "sse4_1", "sse4_2"}
    rows = [set(line.partition(":")[2].split()) for line in Path("/proc/cpuinfo").read_text().splitlines() if line.startswith("flags")]
    if not rows or any(not required.issubset(flags) or not ({"pni", "sse3"} & flags) for flags in rows):
        raise SystemExit("Weblate's NumPy build requires x86-64-v2 CPU features inside the Docker VM. This host/VM does not expose the required features.")
for key in ("POSTGRES_PASSWORD", "WEBLATE_ADMIN_PASSWORD"):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", os.environ.get(key, "")):
        raise SystemExit(f"{key} must contain 64 hexadecimal characters")
if os.environ["POSTGRES_PASSWORD"] == os.environ["WEBLATE_ADMIN_PASSWORD"]:
    raise SystemExit("Database and administrator passwords must be distinct")
if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", os.environ.get("WEBLATE_ADMIN_EMAIL", "")):
    raise SystemExit("Provide the actual owner email in WEBLATE_ADMIN_EMAIL")
os.execv("/app/bin/start", ["/app/bin/start", *sys.argv[1:]])
