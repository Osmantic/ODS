#!/usr/bin/env python3
"""Owner-only controller process; root retains restart and gate authority.

Only fixed JSON hook names cross the pipe. No config, CLI diagnostics, token,
command or arbitrary path is returned to the caller.
"""
import json
import os
import subprocess
import sys
from pathlib import Path
import stat

sys.dont_write_bytecode = True

# The privileged installer places only these reviewed modules together. Isolated
# Python excludes cwd, PYTHONPATH and user site packages; add this protected path.
directory = Path(__file__).resolve().parent
for path in (directory, *directory.parents, *(directory / name for name in (
        "pixel_access_mode.py", "access_mode_config.py", "settings_transaction.py", "pixel_access_protocol.py",
        "pixel_settings", "pixel_settings/__init__.py", "pixel_settings/contract.py", "pixel_settings/projection.py",
        "provider_transaction.py", "pixel_provider", "pixel_provider/__init__.py", "pixel_provider/store.py",
        "pixel_provider/activation_config.py"))):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise RuntimeError("controller program custody unavailable")
sys.path.insert(0, str(directory))
import pixel_access_mode as controller
import pixel_access_protocol as protocol
import settings_transaction
import provider_transaction
from pixel_provider.store import StoreError
from pixel_settings.contract import SettingsError


def emit(value):
    print(json.dumps(value, separators=(",", ":")), flush=True)


def main():
    try:
        request = protocol.request(protocol.read_frame(sys.stdin, protocol.MAX_REQUEST))
    except protocol.ProtocolError:
        emit({"error": "owner-protocol-failed"})
        return
    path = os.path.join(os.environ["HOME"], ".openclaw", "openclaw.json")
    # New integrated installations keep recovery state under the already
    # private config directory. A user's general XDG state parent may validly
    # be group-writable; do not chmod that shared directory or weaken custody.
    # Retain an existing controller state directory and its recovery receipts.
    legacy_state = controller._default_state_dir()
    state_dir = legacy_state if os.path.lexists(legacy_state) else os.path.join(
        os.path.dirname(path), ".ods-access-mode")

    def hook(name):
        emit({"hook": name})
        return protocol.hook_reply(request["operation"], name, protocol.read_frame(sys.stdin, 128))

    def validate(staged):
        env = dict(os.environ, OPENCLAW_CONFIG_PATH=staged)
        try:
            result = subprocess.run([request["openclaw"], "config", "validate"],
                                    env=env, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, timeout=120)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    try:
        if request["operation"].startswith("provider-"):
            if request['operation'] == 'provider-worker-status':
                # Root selected and qualified both executable paths; this
                # subprocess runs only after dropping to the existing owner.
                # Do not load RuntimeStore here: root holds its exclusive lock.
                probe = request['provider_probe']
                payload = {key: probe[key] for key in ('providerDirectory', 'receipt')}
                try:
                    checked = subprocess.run(
                        [probe['python'], '-I', '-S', '-B', probe['launcher'], '--check-runtime'],
                        input=json.dumps(payload) + '\n', text=True, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, timeout=30, check=False)
                    if checked.returncode != 0:
                        raise ValueError('runtime-check-failed')
                    result = protocol.result(request['operation'],
                        protocol.decode_frame(checked.stdout, 128))
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    result = {'ready': False}
            elif request["operation"] == "provider-status":
                result = provider_transaction.provider_status(path, state_dir=state_dir)
            else:
                kwargs = dict(state_dir=state_dir, validate_config=validate,
                    expected_config_sha256=request["config_sha256"], transaction_id=request["transaction_id"],
                    check_no_active_run=lambda: hook("busy"), activate=lambda: hook("provider-activate"))
                result = (provider_transaction.change_provider(path, binding=request["binding"],
                              expected_projection=request.get('expected_projection'), **kwargs)
                          if request["operation"] == "provider-change"
                          else provider_transaction.recover_provider(path, **kwargs))
            emit({"result": result})
            return
        if request["operation"].startswith("settings-"):
            if request["operation"] == "settings-status":
                result = settings_transaction.settings_status(path, state_dir=state_dir)
            else:
                kwargs = dict(state_dir=state_dir, validate_config=validate,
                              expected_config_sha256=request["config_sha256"], transaction_id=request["transaction_id"],
                              check_no_active_run=lambda: hook("busy"), activate=lambda: hook("settings-activate"))
                if request["operation"] == "settings-apply":
                    result = settings_transaction.apply_settings(path, request["preferences"], request["capabilities"],
                                                                 settings_revision=request["settings_revision"], **kwargs)
                else:
                    result = settings_transaction.recover_settings(path, **kwargs)
            emit({"result": result})
            return
        if request["operation"] != "status":
            kwargs = dict(state_dir=state_dir, validate_config=validate, restart=lambda: hook("restart"),
                          check_no_active_run=lambda: hook("busy"),
                          expected_config_sha256=request["config_sha256"])
            if request["operation"] == "full-access":
                controller.enable_full_access(path, confirmed=request["confirmed"], **kwargs)
            elif request["operation"] == "sandboxed":
                controller.restore_sandbox(path, **kwargs)
            else:
                raise RuntimeError("unsupported operation")
        emit({"result": controller.get_status(path, state_dir=state_dir)})
    except controller.AccessModeError as error:
        emit({"error": error.code})
    except (SettingsError, StoreError, protocol.ProtocolError) as error:
        emit({"error": str(error)})
    except Exception:
        emit({"error": "owner-operation-failed"})


if __name__ == "__main__":
    main()
