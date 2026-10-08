"""Connect the managed Laya service to Portal without loading the shared .env."""
from pathlib import Path
import shutil
import subprocess
import sys


def configured_port(root):
    # Reuse the literal grammar used by ODS and Compose; never source .env.
    sys.path.insert(0, str(root / 'extensions/services/dashboard-api'))
    from env_values import parse_env_value
    port = '8017'
    env_file = root / '.env'
    if env_file.exists():
        for line in env_file.read_text(encoding='utf-8').splitlines():
            key, separator, value = line.partition('=')
            if separator and key.strip() == 'LAYA_PORT':
                port = parse_env_value(value) or '8017'
    if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('LAYA_PORT must be an integer from 1 to 65535')
    return port


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: setup.py INSTALL_DIR')
    root = Path(sys.argv[1]).resolve(strict=True)
    entry = root / 'extensions/services/pixel-agent/plugin/laya-setup-cli.mjs'
    if not entry.is_file():
        raise SystemExit('Update ODS to a version with the native Portal Laya integration first.')
    node = shutil.which('node')
    if node is None:
        raise SystemExit('The Portal Node.js runtime is missing from the ODS owner PATH.')
    # The host agent installs library recipes under its configured data root,
    # not extensions/user. Bind the directory actually running this hook.
    marker = Path(__file__).resolve().parent / 'compose.yaml'
    subprocess.run([node, str(entry), str(root), configured_port(root), str(marker)], check=True)


if __name__ == '__main__':
    main()
