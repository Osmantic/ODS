"""Start a private notebook editor without placing its password in argv/logs."""

import os
from pathlib import Path

password = os.environ.pop("MARIMO_PASSWORD")
if len(password) < 16 or password != password.strip() or "\n" in password or "\r" in password:
    raise SystemExit(
        "MARIMO_PASSWORD must contain at least 16 characters "
        "without surrounding whitespace or line breaks"
    )
os.umask(0o077)
token_file = Path("/tmp/ods-marimo-token")
token_file.write_text(password, encoding="utf-8")
del password
os.execvp("marimo", [
    "marimo", "--quiet", "edit", "/workspace", "--headless", "--host", "0.0.0.0",
    "--port", "8080", "--token", "--token-password-file", str(token_file),
    "--no-sandbox",
])
