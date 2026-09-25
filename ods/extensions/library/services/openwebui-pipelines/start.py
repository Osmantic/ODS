import os
import re
import sys

if not re.fullmatch(r"[a-fA-F0-9]{64}", os.environ.get("PIPELINES_API_KEY", "")):
    raise SystemExit("OPENWEBUI_PIPELINES_API_KEY must contain 64 hexadecimal characters")

# Use native application loading without the upstream download/reset shell steps.
os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9099", "--no-proxy-headers"])
