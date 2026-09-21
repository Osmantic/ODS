"""Stage the shared ODS runtime overlay; the caller must validate before replacing."""
import copy, json, os, pathlib, re, stat, sys, tempfile

path = pathlib.Path(sys.argv[1])
if not sys.argv[2].isdigit() or not 1 <= int(sys.argv[2]) <= 65535:
    raise SystemExit("invalid Perplexica service port")
research_port = int(sys.argv[2])
info = path.lstat()
if (not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.getuid() or info.st_mode & 0o077
        or info.st_size > 2 * 1024 * 1024):
    raise SystemExit("unsafe ODS-managed OpenClaw configuration")
parent_info = path.parent.lstat()
if (not stat.S_ISDIR(parent_info.st_mode) or stat.S_ISLNK(parent_info.st_mode)
        or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o022):
    raise SystemExit("unsafe ODS-managed OpenClaw configuration directory")
value = json.loads(path.read_text(encoding="utf-8"))
if not isinstance(value, dict):
    raise SystemExit("ODS-managed OpenClaw configuration must be an object")

providers = value.get("models", {}).get("providers", {})
agents = value.get("agents", {})
agent_list = agents.get("list", []) if isinstance(agents, dict) else []
selected = [item for item in agent_list if isinstance(item, dict) and item.get("id") == "pixel"]
if (not isinstance(providers, dict) or len(providers) != 1
        or next(iter(providers), None) not in {"ods-local", "ods-gateway"}
        or len(selected) != 1):
    raise SystemExit("OpenClaw configuration is outside the ODS Pixel runtime boundary")
provider_id = next(iter(providers))
provider = providers[provider_id]
models = provider.get("models") if isinstance(provider, dict) else None
defaults = agents.get("defaults") if isinstance(agents, dict) else None
session = value.get("session")
model_id = models[0].get("id") if isinstance(models, list) and len(models) == 1 and isinstance(models[0], dict) else None
if (not isinstance(models, list) or len(models) != 1 or not isinstance(models[0], dict)
        or not isinstance(defaults, dict) or not isinstance(session, dict)
        or provider.get("api") != "openai-completions"
        or selected[0].get("model") != f"{provider_id}/{model_id}"):
    raise SystemExit("OpenClaw configuration is outside the ODS Pixel runtime contract")
if provider_id == "ods-local":
    if (provider.get("apiKey") != "local-no-auth"
            or provider.get("baseUrl") != "http://127.0.0.1:11434/v1"
            or not isinstance(model_id, str)
            or models[0].get("name") != f"ODS Local {model_id}"):
        raise SystemExit("OpenClaw local route is outside the ODS Pixel runtime contract")
else:
    gateway_key = provider.get("apiKey")
    gateway_url = provider.get("baseUrl")
    alias_label = "Current" if model_id == "ods/current" else "Default"
    if (model_id not in {"default", "ods/current"}
            or not isinstance(gateway_key, str) or not gateway_key or len(gateway_key) > 4096
            or any(ord(character) < 32 or ord(character) == 127 for character in gateway_key)
            or not isinstance(gateway_url, str)
            or not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}/v1", gateway_url)
            or int(gateway_url.rsplit(":", 1)[1].split("/", 1)[0]) > 65535
            or not isinstance(models[0].get("name"), str)
            or not re.fullmatch(
                rf"ODS {alias_label} \([A-Za-z0-9][A-Za-z0-9._+:/ @(),=-]{{0,255}}\)",
                models[0]["name"],
            )):
        raise SystemExit("OpenClaw gateway route is outside the ODS Pixel runtime contract")

updated = copy.deepcopy(value)
updated_provider = updated["models"]["providers"][provider_id]
updated_defaults = updated["agents"]["defaults"]
updated_agent = next(item for item in updated["agents"]["list"] if isinstance(item, dict) and item.get("id") == "pixel")
updated_agent_experimental = updated_agent.setdefault("experimental", {})
updated_model = updated_provider["models"][0]
updated_session = updated["session"]
updated_agent_sandbox = updated_defaults.setdefault("sandbox", {})
updated_sandbox_docker = updated_agent_sandbox.setdefault("docker", {})
updated_diagnostics = updated.setdefault("diagnostics", {})
updated_compaction = updated_defaults.setdefault("compaction", {})
write_lock = updated_session.setdefault("writeLock", {})
updated_tools = updated.setdefault("tools", {})
updated_also_allow = updated_tools.setdefault("alsoAllow", [])
updated_web = updated_tools.setdefault("web", {})
updated_fetch = updated_web.setdefault("fetch", {})
updated_agent_tools = updated_agent.setdefault("tools", {})
updated_agent_deny = updated_agent_tools.setdefault("deny", [])
updated_sandbox = updated_tools.setdefault("sandbox", {})
updated_sandbox_tools = updated_sandbox.setdefault("tools", {})
updated_sandbox_allow = updated_sandbox_tools.setdefault("allow", [])
updated_plugins = updated.setdefault("plugins", {})
updated_plugin_entries = updated_plugins.setdefault("entries", {}) if isinstance(updated_plugins, dict) else None
updated_pixel_plugin = updated_plugin_entries.setdefault("pixel-ods", {}) if isinstance(updated_plugin_entries, dict) else None
updated_pixel_hooks = updated_pixel_plugin.setdefault("hooks", {}) if isinstance(updated_pixel_plugin, dict) else None
updated_pixel_config = updated_pixel_plugin.setdefault("config", {}) if isinstance(updated_pixel_plugin, dict) else None
if not isinstance(updated_compaction, dict):
    raise SystemExit("OpenClaw compaction configuration must be an object")
if not isinstance(write_lock, dict):
    raise SystemExit("OpenClaw session write-lock configuration must be an object")
if not isinstance(updated_diagnostics, dict):
    raise SystemExit("OpenClaw diagnostics configuration must be an object")
if not isinstance(updated_agent_sandbox, dict) or not isinstance(updated_sandbox_docker, dict):
    raise SystemExit("OpenClaw sandbox configuration must be an object")
if (not isinstance(updated_tools, dict)
        or not isinstance(updated_also_allow, list)
        or not all(isinstance(item, str) for item in updated_also_allow)
        or not isinstance(updated_agent_tools, dict)
        or not isinstance(updated_agent_experimental, dict)
        or not isinstance(updated_agent_deny, list)
        or not all(isinstance(item, str) for item in updated_agent_deny)
        or not isinstance(updated_sandbox, dict)
        or not isinstance(updated_sandbox_tools, dict)
        or not isinstance(updated_sandbox_allow, list)
        or not all(isinstance(item, str) for item in updated_sandbox_allow)
        or not isinstance(updated_plugins, dict)
        or not isinstance(updated_plugin_entries, dict)
        or not isinstance(updated_pixel_plugin, dict)
        or not isinstance(updated_pixel_hooks, dict)
        or not isinstance(updated_pixel_config, dict)):
    raise SystemExit("OpenClaw tool policy is outside the ODS Pixel runtime contract")
if not isinstance(updated_web, dict) or not isinstance(updated_fetch, dict):
    raise SystemExit("OpenClaw web tool policy is outside the ODS Pixel runtime contract")
openclaw_home = pathlib.Path(sys.argv[4]) if len(sys.argv) > 4 else pathlib.Path.home() / ".openclaw"
if not openclaw_home.is_absolute() or ".." in openclaw_home.parts:
    raise SystemExit("absolute ODS OpenClaw home required")
exec_control_bind = "{}:/run/pixel-ods-control:ro".format(openclaw_home / ".ods-exec-control")
existing_binds = updated_sandbox_docker.get("binds", [])
if existing_binds not in ([], [exec_control_bind]):
    raise SystemExit("OpenClaw sandbox binds are outside the ODS Pixel runtime contract")
# OpenClaw accepts this owner-private source only with its explicit external
# bind opt-in. ODS still pins the sole source and destination above, validates
# the host tree owner/mode, and exposes it read-only inside the sandbox.
updated_sandbox_docker["binds"] = [exec_control_bind]
updated_sandbox_docker["dangerouslyAllowExternalBindSources"] = True
# Model budgets must preserve native web-search provider choices.
# OpenClaw validates the complete candidate below; search provisioning and
# readiness belong to bootstrap, not this context/sandbox budget overlay.
updated_provider["timeoutSeconds"] = 1800
updated_defaults["timeoutSeconds"] = 1800
updated_defaults["bootstrapMaxChars"] = 32000
updated_defaults["bootstrapTotalMaxChars"] = 96000
updated_defaults["contextInjection"] = "continuation-skip"
updated_agent_context_limits = updated_agent.setdefault("contextLimits", {})
# Tool Search below keeps schemas compact for every model. The separate
# upstream localModelLean switch removes cron and browser from the catalog,
# even when policy otherwise permits them. Keep that capability filter off;
# model size must not silently remove core agent features. Existing explicit
# tool denials, sandbox policy, and ODS authority checks still apply.
updated_agent_experimental["localModelLean"] = False
updated_tools["toolSearch"] = {
    "enabled": True,
    "mode": "tools",
    "searchDefaultLimit": 5,
    "maxSearchLimit": 10,
}
# The finalization hook consumes only per-run structured guard state and
# does not inspect or persist conversation text. OpenClaw nevertheless requires
# this explicit trust bit before any installed plugin may register the hook.
updated_pixel_hooks["allowConversationAccess"] = True
context_window = updated_model.get("contextWindow")
model_max_tokens = updated_model.get("maxTokens")
if (type(context_window) is not int or type(model_max_tokens) is not int
        or context_window < 4096 or not 1 <= model_max_tokens <= context_window):
    raise SystemExit("OpenClaw model limits are outside the ODS Pixel runtime contract")
# Preserve complete upstream workspace contracts when the selected route can
# carry and follow them. Small checkpoints and compact contexts receive the
# equivalent concise plugin core and on-demand capability contracts. This
# generic size/context profile never rejects a model or hides a callable tool.
compact_context = context_window < 32768
model_label = "{} {}".format(updated_model.get("id", ""), updated_model.get("name", "")).casefold()
parameter_markers = re.findall(
    r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*b(?![a-z0-9])",
    model_label,
)
small_model = any(float(marker) <= 4 for marker in parameter_markers)
lean_prompt = compact_context or small_model
updated_pixel_config["modelContextWindow"] = context_window
updated_pixel_config["leanPrompt"] = lean_prompt
updated_pixel_config["perplexicaPort"] = research_port
if sys.argv[3]:
    answers_path = pathlib.Path(sys.argv[3])
    answers_info = answers_path.lstat()
    if (not stat.S_ISREG(answers_info.st_mode) or stat.S_ISLNK(answers_info.st_mode)
            or answers_info.st_nlink != 1 or answers_info.st_uid != os.getuid()
            or answers_info.st_mode & 0o077 or answers_info.st_size > 2 * 1024 * 1024):
        raise SystemExit("unsafe ODS Pixel route identity contract")
    answers = json.loads(answers_path.read_text(encoding="utf-8"))
    route_fingerprint = answers.get("modelRouteFingerprint")
    if route_fingerprint is not None and (answers.get("modelProvider") != "ods-gateway"
            or not isinstance(route_fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", route_fingerprint)):
        raise SystemExit("invalid ODS Pixel route identity")
    if route_fingerprint is None:
        updated_pixel_config.pop("modelRouteFingerprint", None)
    else:
        updated_pixel_config["modelRouteFingerprint"] = route_fingerprint
updated_agent["bootstrapMaxChars"] = 2000 if lean_prompt else 14000
updated_agent["bootstrapTotalMaxChars"] = 6000 if lean_prompt else 36000
updated_agent["contextInjection"] = "never" if lean_prompt else "continuation-skip"
# Bound each live tool result by the real context capacity of the selected model.
# This is capability-based prompt shaping, never a model allowlist: failures
# retain OpenClaw diagnostic head/tail projection and every tool remains
# callable, while one large read or verbose suite cannot crowd out the next
# model continuation on compact local contexts.
updated_agent_context_limits["toolResultMaxChars"] = max(
    4000,
    min(16000, context_window // 4),
)
# The OpenClaw OpenAI-compatible transport applies a 1.25 input estimate after
# the pre-prompt compaction check. Leave enough precheck headroom for the real
# model output ceiling before that later transport clamp can reduce a
# continuation to one token. This remains context-derived for every model and
# does not change its tools or authority.
updated_compaction["reserveTokens"] = (
    context_window + 4 * model_max_tokens + 4
) // 5
# The computed reserve already includes output and transport headroom.
# A redundant half-window floor can reject otherwise usable compacted history.
updated_compaction["reserveTokensFloor"] = 0
# The fixed OpenClaw 20K keep-recent default is larger than every compact ODS
# profile. Scale it for all contexts so compaction always drops real history
# instead of writing an empty no-op summary and blocking the recovery retry.
updated_compaction["keepRecentTokens"] = max(
    512,
    min(20000, context_window // 16),
)
# The legacy OpenAI-completions transport in OpenClaw 2026.6.33 coerces the literal
# reasoning effort "off" with Boolean("off"), which wrongly sends
# chat_template_kwargs.enable_thinking=true. With the llama.cpp Qwen template
# that can spend the complete output budget in hidden reasoning after a tool
# call and leave no user-visible answer. When ODS reasoning is disabled, keep
# the model non-reasoning and omit the Qwen compatibility knob so the llama.cpp
# independently pinned no-think default remains authoritative. When the owner
# explicitly enables reasoning, advertise the capability and use a real
# non-off effort so both affected and corrected OpenClaw transports agree.
model_reasoning = updated_model.get("reasoning", False)
if type(model_reasoning) is not bool:
    raise SystemExit("OpenClaw model reasoning configuration must be boolean")
updated_model["reasoning"] = model_reasoning
if "qwen" in model_label and model_reasoning:
    model_compat = updated_model.setdefault("compat", {})
    if not isinstance(model_compat, dict):
        raise SystemExit("OpenClaw Qwen compatibility configuration must be an object")
    model_compat["thinkingFormat"] = "qwen-chat-template"
    updated_agent["thinkingDefault"] = "low"
else:
    updated_model.pop("compat", None)
    updated_agent.pop("thinkingDefault", None)
updated_agent_params = updated_agent.setdefault("params", {})
if not isinstance(updated_agent_params, dict):
    raise SystemExit("OpenClaw Pixel agent parameters must be an object")
if "qwen" in model_label:
    template_kwargs = updated_agent_params.setdefault("chat_template_kwargs", {})
    if not isinstance(template_kwargs, dict):
        raise SystemExit("OpenClaw Pixel chat-template parameters must be an object")
    template_kwargs["enable_thinking"] = model_reasoning
else:
    template_kwargs = updated_agent_params.get("chat_template_kwargs")
    if isinstance(template_kwargs, dict):
        template_kwargs.pop("enable_thinking", None)
        if not template_kwargs:
            updated_agent_params.pop("chat_template_kwargs", None)
    if not updated_agent_params:
        updated_agent.pop("params", None)
# Small or compact local checkpoints can get trapped repeating a valid prefix inside a
# JSON tool argument even though their plain-text generation is healthy. The
# standard OpenAI sampling controls below made the same 2B route terminate its
# minimal write call in 81 tokens instead of exhausting 1024. Apply the profile
# by generic size/context capability rather than an allowlist or readiness
# gate, and remove it transactionally when a larger route is promoted.
compact_sampling = {
    "temperature": 0.7,
    "topP": 0.8,
    "frequencyPenalty": 0.6,
    "presencePenalty": 0.2,
}
if lean_prompt:
    updated_agent["params"] = updated_agent_params
    updated_agent_params.update(compact_sampling)
else:
    for key in compact_sampling:
        updated_agent_params.pop(key, None)
if not updated_agent_params:
    updated_agent.pop("params", None)
# A CPU-only model call can emit no progress while evaluating a long prompt.
# Let the 30-minute provider own its terminal timeout, then retain one minute
# for the OpenClaw stalled-session recovery before the 32-minute host ingress.
updated_diagnostics["stuckSessionAbortMs"] = 1860000
write_lock["maxHoldMs"] = 1920000
write_lock["staleMs"] = 3600000
updated_tools["loopDetection"] = {
    "enabled": True,
    "historySize": 12,
    "warningThreshold": 2,
    "unknownToolThreshold": 2,
    "criticalThreshold": 4,
    "globalCircuitBreakerThreshold": 6,
    "detectors": {
        "genericRepeat": True,
        "knownPollNoProgress": True,
        "pingPong": True,
    },
}
# Search stays private through loopback-only SearXNG. Page retrieval uses
# OpenClaw public-network SSRF guard with deliberately tighter ODS bounds;
# private/link-local targets and trusted environment proxies remain disabled.
updated_fetch.update({
    "enabled": True,
    "maxChars": 12000,
    "maxCharsCap": 20000,
    "maxResponseBytes": 1000000,
    "timeoutSeconds": 20,
    "cacheTtlMinutes": 15,
    "maxRedirects": 3,
    "readability": True,
    "useTrustedEnvProxy": False,
    "ssrfPolicy": {
        "allowRfc2544BenchmarkRange": False,
        "allowIpv6UniqueLocalRange": False,
    },
})
updated_agent_tools["deny"] = [
    item for item in updated_agent_deny
    if item not in {
        "web_search", "web_fetch", "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_extensions", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
        "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
        "pixel_ods_research", "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview", "pixel_ods_ask_user", "pixel_ods_goal", "pixel_ods_activity", "pixel_ods_history",
        "pixel_web_extract"
    }
]
updated_also_allow = [item for item in updated_also_allow if item != "pixel_web_extract"]
updated_sandbox_allow = [item for item in updated_sandbox_allow if item != "pixel_web_extract"]
for extension_tool in (
    "cron", "create_goal", "get_goal", "update_goal", "update_plan",
    "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_extensions", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
    "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
    "pixel_ods_research", "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview", "pixel_ods_ask_user", "pixel_ods_goal", "pixel_ods_activity", "pixel_ods_history"
):
    if extension_tool not in updated_also_allow:
        updated_also_allow.append(extension_tool)
for permitted_tool in (
    "cron", "create_goal", "get_goal", "update_goal", "update_plan",
    "web_search", "web_fetch", "pixel_ods_status", "pixel_ods_apps_list", "pixel_ods_extensions", "pixel_ods_host_observe", "pixel_ods_host_command_propose",
    "pixel_ods_evidence_report", "pixel_ods_evidence_readback",
    "pixel_ods_research", "pixel_ods_web_extract", "pixel_ods_download_promote", "pixel_ods_workspace_preview", "pixel_ods_ask_user", "pixel_ods_goal", "pixel_ods_activity", "pixel_ods_history"
):
    if permitted_tool not in updated_sandbox_allow:
        updated_sandbox_allow.append(permitted_tool)
updated_tools["alsoAllow"] = sorted(set(updated_also_allow))
updated_sandbox_tools["allow"] = sorted(set(updated_sandbox_allow))
if updated == value:
    print("unchanged")
    raise SystemExit(0)

fd, temporary = tempfile.mkstemp(prefix=".ods-pixel-runtime-budget.", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(updated, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temporary, 0o600)
    print(temporary)
except BaseException:
    if os.path.exists(temporary):
        os.unlink(temporary)
    raise
