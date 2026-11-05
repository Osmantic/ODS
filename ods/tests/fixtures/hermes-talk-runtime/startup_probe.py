"""Run after the real ODS auth bootstrap and upstream s6/UID remapping."""
import json
import os
from pathlib import Path
import tui_gateway.server as server

assert os.getuid() == 10000, os.getuid()
assert os.getgid() == 10000, os.getgid()
assert os.environ.get("PYTHONPATH") == "/opt/ods-hermes-runtime"
assert os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_PASSWORD")
assert os.environ.get("HERMES_DASHBOARD_BASIC_AUTH_SECRET")
assert Path("/run/ods-hermes-auth/policy.json").is_file()

result = server.handle_request({"jsonrpc": "2.0", "id": "boot-proof",
                                "method": "ods.talk.capabilities", "params": {}})
assert result["result"] == {"manual_resume": True, "version": "1"}, result
print(json.dumps({"fixture": "startup_probe", "uid": os.getuid(), "gid": os.getgid(),
                  "auth_present": True, "manual_resume": True}))
