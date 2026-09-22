"""Build a private htpasswd file without leaking credentials to argv or logs."""
import os
import re
from pathlib import Path

import bcrypt


def main():
    password = os.environ.pop("RADICALE_PASSWORD", "")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", password):
        raise SystemExit("RADICALE_PASSWORD must contain exactly 64 hexadecimal characters")
    os.umask(0o077)
    directory = Path("/tmp/ods-radicale")
    directory.mkdir(mode=0o700, exist_ok=True)
    hashed = bcrypt.hashpw(password.encode("ascii"), bcrypt.gensalt(rounds=12))
    (directory / "users").write_bytes(b"ods:" + hashed + b"\n")
    os.execvp("python", ["python", "-m", "radicale", "--config", "/etc/radicale/config"])


if __name__ == "__main__":
    main()
