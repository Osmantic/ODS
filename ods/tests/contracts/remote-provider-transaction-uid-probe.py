import importlib.util
import json
import os
from pathlib import Path


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


source = Path("/candidate")
root = Path("/ods")
selector = load("selector", source / "scripts/extension-selection.py")
tx = load(
    "transaction", source / "installers/windows/lib/remote-provider-transaction.py"
)
inode = (root / "data/.extensions-lock").stat().st_ino
flags = ["-f", "docker-compose.base.yml", "-f", "docker-compose.override.yml"]
steps = []
for service in tx.SERVICES:
    steps.append(selector.run("enable", root, service))
enabled = tx.transact(root, source, {"operation": "flags", "flags": flags})
assert len(enabled["flags"]) == 8
for service in reversed(tx.SERVICES):
    steps.append(selector.run("disable", root, service))
disabled = tx.transact(root, source, {"operation": "flags", "flags": enabled["flags"]})
assert disabled["flags"] == flags
assert (root / ".compose-flags").read_text() == " ".join(flags)
assert (root / "data/.extensions-lock").stat().st_ino == inode
print(
    json.dumps(
        {
            "uid": os.getuid(),
            "steps": steps,
            "sameLockInode": True,
            "flagsPublishedAndReadBack": True,
        }
    )
)
