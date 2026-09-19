"""Prepare private native authentication configuration before starting MLflow."""

import configparser
import os
from pathlib import Path
import tempfile


def credential(name, minimum):
    value = os.environ.pop(name, "")
    if not minimum <= len(value) <= 128 or any(not 33 <= ord(char) <= 126 for char in value):
        raise SystemExit(f"{name} must contain {minimum} to 128 printable ASCII characters without spaces")
    return value


password = credential("MLFLOW_ADMIN_PASSWORD", 16)
secret = credential("MLFLOW_SECRET_KEY", 32)
os.umask(0o077)
directory = Path(tempfile.mkdtemp(prefix="ods-mlflow-"))
config = configparser.ConfigParser()
config["mlflow"] = {
    "default_permission": "NO_PERMISSIONS",
    "database_uri": "sqlite:////data/auth.db",
    "admin_username": "ods",
    # The native reader uses ConfigParser interpolation; preserve literal %.
    "admin_password": password.replace("%", "%%"),
    "grant_default_workspace_access": "false",
    "auth_cache_ttl_seconds": "0",
}
auth_path = directory / "auth.ini"
with auth_path.open("w") as stream:
    config.write(stream)
os.environ["MLFLOW_AUTH_CONFIG_PATH"] = str(auth_path)
os.environ["MLFLOW_FLASK_SERVER_SECRET_KEY"] = secret
os.execvp("mlflow", [
    "mlflow", "server", "--app-name", "basic-auth", "--host", "0.0.0.0",
    "--port", "5000", "--workers", "1",
    "--backend-store-uri", "sqlite:////data/tracking.db",
    "--artifacts-destination", "/data/artifacts", "--serve-artifacts",
])
