#!/usr/bin/env python3
"""Shared ODS Pixel onboarding contract for native host installers."""

import json, os, pathlib, re, stat, sys, tempfile

(out, openclaw_bin, home, model, context, max_tokens, reasoning,
 gateway_alias, gateway_label, model_gateway_port, pixel_gateway_port, gateway_key_path,
 search_port, plugin_path, plugin_digest, web_search_provider, parallel_path, parallel_digest) = sys.argv[1:]
if (not context.isdigit() or not 4096 <= int(context) <= 10_000_000
        or not max_tokens.isdigit() or not 1 <= int(max_tokens) <= int(context)
        or reasoning not in {"true", "false"}
        or not search_port.isdigit() or not 1 <= int(search_port) <= 65535):
    raise SystemExit("invalid ODS Pixel model or search budget")
if web_search_provider not in {"searxng", "parallel-free"}:
    raise SystemExit("invalid native search provider")
if web_search_provider == "parallel-free" and (
        not pathlib.Path(parallel_path).is_absolute()
        or not re.fullmatch(r"[0-9a-f]{64}", parallel_digest)):
    raise SystemExit("native search requires a provisioned and verified parallel plugin")
gateway_key_path = pathlib.Path(gateway_key_path)
gateway_key_info = gateway_key_path.lstat()
if (not stat.S_ISREG(gateway_key_info.st_mode) or stat.S_ISLNK(gateway_key_info.st_mode)
        or gateway_key_info.st_nlink != 1 or gateway_key_info.st_uid != os.getuid()
        or gateway_key_info.st_mode & 0o077 or gateway_key_info.st_size > 4096):
    raise SystemExit("unsafe ODS Pixel gateway credential")
gateway_key = gateway_key_path.read_text(encoding="utf-8")
if gateway_alias not in {"default", "ods/current"}:
    raise SystemExit("invalid ODS Pixel gateway alias")
if gateway_label not in {"Default", "Current"}:
    raise SystemExit("invalid ODS Pixel gateway label")
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{0,255}", model):
    raise SystemExit("invalid ODS Pixel model id")
if (not model_gateway_port.isdigit() or not 1 <= int(model_gateway_port) <= 65535
        or not pixel_gateway_port.isdigit() or not 1 <= int(pixel_gateway_port) <= 65535
        or not gateway_key or len(gateway_key) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in gateway_key)):
    raise SystemExit("invalid ODS Pixel gateway route")
home = pathlib.Path(home)
path = pathlib.Path(out)
# A source/plugin upgrade is not a request to reset the owner's output budget.
# Preserve it for the same model route and context; explicit model settings are
# still applied by _ods_pixel_update_onboarding_model.
route_fingerprint = None
try:
    previous_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
except FileNotFoundError:
    if path.is_symlink():
        raise SystemExit("ODS Pixel onboarding contract cannot be a symlink")
else:
    with os.fdopen(previous_fd, "rb") as previous_file:
        info = os.fstat(previous_file.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o077
                or info.st_size > 2 * 1024 * 1024):
            raise SystemExit("unsafe existing ODS Pixel onboarding contract")
        previous_bytes = previous_file.read(2 * 1024 * 1024 + 1)
    if len(previous_bytes) > 2 * 1024 * 1024:
        raise SystemExit("oversized existing ODS Pixel onboarding contract")
    previous = json.loads(previous_bytes)
    if (not isinstance(previous, dict)
            or previous.get("deploymentName") != "ods-default" or previous.get("agentId") != "pixel"
            or type(previous.get("modelContextWindow")) is not int
            or type(previous.get("modelMaxTokens")) is not int
            or type(previous.get("modelReasoning")) is not bool
            or not 4096 <= previous["modelContextWindow"] <= 10_000_000
            or not 1 <= previous["modelMaxTokens"] <= previous["modelContextWindow"]):
        raise SystemExit("invalid existing ODS Pixel model contract")
    same_model = {
        "modelProvider": "ods-gateway", "modelId": gateway_alias,
        "modelName": f"ODS {gateway_label} ({model})",
        "modelBaseUrl": f"http://127.0.0.1:{model_gateway_port}/v1",
        "modelContextWindow": int(context), "modelReasoning": reasoning == "true",
    }
    if all(previous.get(key) == value for key, value in same_model.items()):
        max_tokens = str(previous["modelMaxTokens"])
        route_fingerprint = previous.get("modelRouteFingerprint")
        if route_fingerprint is not None and (not isinstance(route_fingerprint, str)
                or not re.fullmatch(r"[a-f0-9]{64}", route_fingerprint)):
            raise SystemExit("invalid existing ODS Pixel route identity")
payload = {
    "deploymentProfile": "prepared",
    "capabilityProfile": "engineering-operator",
    "ownerName": "ODS Owner",
    "organization": "Local ODS",
    "deploymentName": "ods-default",
    "timeZone": "UTC",
    "agentId": "pixel",
    "agentName": "Pixel",
    "openclawBin": openclaw_bin,
    "openclawHome": str(home / ".openclaw"),
    "installDir": str(home / ".local" / "share" / "pixel"),
    "workspace": str(home / ".openclaw" / "workspace-pixel"),
    "modelProvider": "ods-gateway",
    "modelId": gateway_alias,
    "modelName": f"ODS {gateway_label} ({model})",
    "modelBaseUrl": f"http://127.0.0.1:{model_gateway_port}/v1",
    "modelApiKey": gateway_key,
    "modelReasoning": reasoning == "true",
    "modelContextWindow": int(context),
    "modelMaxTokens": int(max_tokens),
    "modelPrivateHosts": [],
    "webSearchProvider": web_search_provider,
    "searxngBaseUrl": f"http://127.0.0.1:{search_port}",
    "embeddingModel": "embeddinggemma-300m-qat-Q8_0.gguf",
    "embeddingCache": str(home / ".cache" / "openclaw" / "embeddings"),
    "googleAccount": "ods@localhost.local",
    "calendarId": "primary",
    "gatewayPort": int(pixel_gateway_port),
    "gatewayExtensions": [{
        "id": "pixel-ods",
        "path": plugin_path,
        "sha256": plugin_digest,
        "tools": ["pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_extensions", "pixel_ods_host_observe", "pixel_ods_host_command_propose", "pixel_ods_evidence_report", "pixel_ods_evidence_readback", "pixel_ods_research", "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview", "pixel_ods_ask_user", "pixel_ods_goal", "pixel_ods_activity", "pixel_ods_history", "pixel_ods_skill", "pixel_ods_extension_proposal", "pixel_ods_source_proposal", "pixel_ods_python_library_proposal", "pixel_ods_extension_request_status", "pixel_ods_extension_request_prepare", "pixel_ods_extension_request_advance"],
    }],
    "localCapabilityPacks": [],
    "agentSkills": [],
    "emailLimbEnabled": False,
    "calendarLimbEnabled": False,
    "calendarDirectEnabled": False,
    "socialLimbEnabled": False,
    "webLimbEnabled": False,
    "operationsLimbEnabled": True,
    "operationsPolicyFile": str(path.parent / "operations-policy.json"),
    "frontierLimbEnabled": False,
    "frontierAuthMode": "api-key",
    # Pixel still validates the managed Frontier policy while the limb is
    # disabled. Use its smallest built-in budget rather than "custom", which
    # is reserved for a separate private policy and otherwise renders an empty
    # budget object during configure.
    "frontierBudgetProfile": "starter",
    "frontierTaskPacks": [],
    "operationsActionPacks": [],
}
if route_fingerprint is not None:
    payload["modelRouteFingerprint"] = route_fingerprint
if web_search_provider == "parallel-free":
    payload["gatewayExtensions"].append({"id": "parallel", "path": parallel_path, "sha256": parallel_digest})
path.parent.mkdir(parents=True, exist_ok=True)
if path.is_symlink():
    raise SystemExit("ODS Pixel onboarding contract cannot be a symlink")
if path.exists():
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > 2 * 1024 * 1024:
        raise SystemExit("invalid existing ODS Pixel onboarding contract")
content = json.dumps(payload, indent=2, sort_keys=True) + "\n"
fd, temporary = tempfile.mkstemp(prefix=".pixel-onboarding.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
finally:
    if os.path.exists(temporary):
        os.unlink(temporary)
