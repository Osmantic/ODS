"""Start the pinned CalDAV/CardDAV server with a private per-start credential file."""

import os
from pathlib import Path
import tempfile

import bcrypt

password = os.environ.pop("RADICALE_PASSWORD", "").encode("utf-8")
if not 16 <= len(password) <= 72:
    raise SystemExit("RADICALE_PASSWORD must contain 16 to 72 UTF-8 bytes")

os.umask(0o077)
private = Path(tempfile.mkdtemp(prefix="ods-radicale-"))
users = private / "users"
users.write_text("ods:" + bcrypt.hashpw(password, bcrypt.gensalt()).decode("ascii") + "\n")
del password
config = private / "config"
config.write_text(f"""[server]
hosts = 0.0.0.0:5232
max_connections = 8
max_content_length = 8388608
timeout = 30

[auth]
type = htpasswd
htpasswd_filename = {users}
htpasswd_encryption = bcrypt
cache_logins = False

[rights]
type = owner_only

[storage]
type = multifilesystem
filesystem_folder = /var/lib/radicale/collections

[logging]
level = info
mask_passwords = True
""")
# The official image relocates its venv; preserve its explicit Python launch
# instead of following the console script's build-time interpreter path.
os.execv("/app/bin/python", ["/app/bin/python", "/app/bin/radicale", "--config", str(config)])
