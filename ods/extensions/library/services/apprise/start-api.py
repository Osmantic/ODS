"""Materialize the operator's fixed native route and run the shipped WSGI app."""

import os
from pathlib import Path

import apprise

os.umask(0o077)
if len(os.environ["SECRET_KEY"]) < 32:
    raise ValueError("APPRISE_DJANGO_SECRET must contain at least 32 characters")
configuration = os.environ["APPRISE_CONFIG_TEXT"]
parsed = apprise.AppriseConfig(recursion=0)
if not parsed.add_config(configuration, format=apprise.ConfigFormat.TEXT) or not parsed.servers():
    raise ValueError("APPRISE_CONFIG_TEXT must contain a usable native text configuration")
for name in ("config", "store", "attach", "plugin"):
    Path("/tmp/apprise", name).mkdir(parents=True, exist_ok=True)
Path("/tmp/apprise/config/ods.cfg").write_text(configuration, encoding="utf-8")
os.execvp("gunicorn", [
    "gunicorn", "--config", "/opt/apprise/webapp/gunicorn.conf.py",
    "--bind", "127.0.0.1:8000", "--worker-class", "sync",
    "--workers", "1", "--timeout", "30", "--graceful-timeout", "30",
    "--no-control-socket",
    "core.wsgi:application",
])
