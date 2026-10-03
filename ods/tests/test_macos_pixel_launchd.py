"""Pure contracts for the native macOS LaunchDaemon renderer."""
import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "pixel_macos_launchd", ROOT / "installers/macos/lib/pixel-access-launchd.py")
launchd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launchd)


def gateway_document():
    return launchd.native_gateway_document(
        owner="gabriel", group="staff",
        program_arguments=["/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
                           "/usr/bin/sandbox-exec", "-f", "/etc/ods/pixel-gateway.sb",
                           "/opt/ods-launcher", "gateway", "run", "--port", "18789"],
        working_directory="/Users/gabriel/ods/data/pixel-native",
        environment={"HOME": "/Users/gabriel"},
        stdout_path="/Users/gabriel/Library/Logs/ODS/gateway.log",
        stderr_path="/Users/gabriel/Library/Logs/ODS/gateway.log")


def test_gateway_document_is_owner_scoped_and_uses_clean_environment():
    document = gateway_document()
    assert document["Label"] == launchd.GATEWAY_LABEL
    assert document["UserName"] == "gabriel"
    assert document["GroupName"] == "staff"
    assert document["ProgramArguments"][:2] == ["/usr/bin/env", "-i"]
    assert document["EnvironmentVariables"] == {"HOME": "/Users/gabriel"}
    assert launchd.encode(document) == launchd.encode(json.loads(json.dumps(document)))


def test_provider_definition_ignores_only_its_two_managed_env_assignments():
    before = gateway_document()
    after = dict(before, ProgramArguments=[
        "/usr/bin/env", "-i", "PATH=/usr/bin:/bin",
        'OPENCLAW_REQUIRED_PLUGINS={"version":1}',
        'PIXEL_ODS_PROVIDER_DEPLOYMENT={"binding":{}}',
        *before["ProgramArguments"][3:]])
    assert launchd.gateway_definition(before) == launchd.gateway_definition(after)
    changed = dict(after, ProgramArguments=[*after["ProgramArguments"][:-1], "18790"])
    assert launchd.gateway_definition(changed) != launchd.gateway_definition(after)


def test_gateway_uses_openclaw_native_defaults_without_changing_pixel_supervision():
    document = gateway_document()
    assert document["ProcessType"] == "Interactive"
    assert document["Umask"] == 0o077
    assert document["StandardInPath"] == "/dev/null"
    assert document["KeepAlive"] == {"SuccessfulExit": False}
    assert document["UserName"] != "root"
    for key, value in (("ProcessType", "Background"), ("Umask", 0o022),
                       ("StandardInPath", "/tmp/input")):
        assert launchd.gateway_definition(dict(document, **{key: value})) != launchd.gateway_definition(document)


def test_binding_and_access_settings_pin_the_system_daemon():
    document = gateway_document()
    receipt = launchd.binding(document, owner="gabriel", executable="/opt/node", uid=501, gid=20)
    assert receipt["target"] == "system/" + launchd.GATEWAY_LABEL
    assert receipt["plist"] == str(launchd.GATEWAY_PLIST)
    settings = launchd.access_settings(
        install_dir="/Users/gabriel/ods", owner="gabriel", openclaw_bin="/opt/openclaw",
        gateway_port=18789, binding_value=receipt, settings_data_dir="/Users/gabriel/ods/data",
        edge_owner_key_sha256="a" * 64)
    assert settings["gateway_binding"] == receipt
    assert settings["gateway_process"] == {"uid": 501, "gid": 20, "executable": "/opt/node"}
    assert json.loads(launchd.plan_json(gateway=document, access=settings))["access"]["edge_owner_key_sha256"] == "a" * 64


@pytest.mark.parametrize("target,plist", [
    ("gui/501/com.ods.pixel-native-gateway", "/Users/gabriel/Library/LaunchAgents/com.ods.pixel-native-gateway.plist"),
    ("system/com.other", "/Library/LaunchDaemons/com.ods.pixel-native-gateway.plist"),
])
def test_binding_rejects_noncanonical_daemon_identity(target, plist):
    with pytest.raises(launchd.LaunchdPlanError):
        launchd.binding(gateway_document(), owner="gabriel", executable="/opt/node",
                        plist=plist, target=target, uid=501, gid=20)


def test_root_access_daemon_uses_fixed_interpreter_and_no_owner_environment():
    document = launchd.access_daemon_document()
    assert document["Label"] == launchd.ACCESS_LABEL
    assert document["UserName"] == "root"
    assert document["ProgramArguments"] == ["/usr/bin/python3", "-I", str(launchd.ACCESS_PROGRAM)]
    assert document["EnvironmentVariables"] == {"PYTHONNOUSERSITE": "1"}
