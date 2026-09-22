import os
import re
import sys

for key in ("SECRET_KEY", "DB_PASSWORD"):
    if not re.fullmatch(r"[0-9a-fA-F]{64}", os.environ.get(key, "")):
        raise SystemExit(f"{key} must contain 64 hexadecimal characters")
if os.environ["SECRET_KEY"] == os.environ["DB_PASSWORD"]:
    raise SystemExit("Signing and database secrets must be distinct")
os.execvp(sys.argv[1], sys.argv[1:])
