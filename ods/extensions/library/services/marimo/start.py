import os
from pathlib import Path
import re
import sys

token = os.environ.pop("MARIMO_TOKEN", "")
if not re.fullmatch(r"[a-fA-F0-9]{64}", token):
    raise SystemExit("MARIMO_TOKEN must contain exactly 64 hexadecimal characters")
os.umask(0o077)
directory = Path('/tmp/ods-marimo')
directory.mkdir(mode=0o700, exist_ok=True)
token_file = directory / 'token'
token_file.write_text(token, encoding='utf-8')
config = Path('/data/home/.marimo.toml')
if not config.exists():
    config.write_text('[package_management]\nmanager = "pip"\n', encoding='utf-8')
os.execv(sys.executable, [sys.executable, '-m', 'marimo', 'edit', '/workspace', '--host', '0.0.0.0', '--port', '8080', '--headless', '--token', '--token-password-file', str(token_file), '--no-sandbox'])
