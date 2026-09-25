#!/usr/bin/env python3
"""Static contract tests for Perplexica's ODS entrypoint patch."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

try:
    import pytest
except ModuleNotFoundError:
    pytest = None


ROOT = Path(__file__).resolve().parents[1]
ENV_SCHEMA = ROOT / ".env.schema.json"
SERVICE_DIR = ROOT / "extensions" / "services" / "perplexica"
COMPOSE = SERVICE_DIR / "compose.yaml"
MANIFEST = SERVICE_DIR / "manifest.yaml"
ENTRYPOINT = SERVICE_DIR / "docker-entrypoint.sh"
SYNC_SCRIPT = SERVICE_DIR / "sync-model-config.js"
SEARCH_SYNC_SCRIPT = SERVICE_DIR / "sync-search-config.js"
WHISPER_COMPOSE = ROOT / "extensions" / "services" / "whisper" / "compose.yaml"
BRAVE_DIR = ROOT / "extensions" / "services" / "brave-search"
HEALTH_PHASE = ROOT / "installers" / "phases" / "12-health.sh"
SUMMARY_PHASE = ROOT / "installers" / "phases" / "13-summary.sh"
REPAIR_SCRIPT = ROOT / "scripts" / "repair" / "repair-perplexica.sh"
RELEASE = ROOT / "config" / "perplexica-release.json"
DEPENDENCY_LOCK = ROOT / "config" / "dependency-lock.json"
IMAGES_PHASE = ROOT / "installers" / "phases" / "08-images.sh"


def _node_cmd_or_skip() -> str | None:
    node = shutil.which("node")
    if node:
        return node
    if pytest is not None:
        pytest.skip("Node.js is required")
    print("[SKIP] Node.js is required")
    return None


def _bash_cmd_or_skip() -> str | None:
    bash = shutil.which("bash")
    if bash:
        return bash
    if pytest is not None:
        pytest.skip("bash is required")
    print("[SKIP] bash is required")
    return None


def _slice_block(path: Path, start_marker: str, end_line: str) -> str:
    """Return the shell block starting at `start_marker` up to `end_line`.

    `end_line` is matched against the whole (untrimmed) line so indentation
    picks the closing `fi` of the intended block rather than a nested one.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if start_marker in line)
    end = next(i for i in range(start + 1, len(lines)) if lines[i].rstrip() == end_line)
    return "\n".join(lines[start:end + 1])


def _resolve_expected_model(bash: str, block: str, env: dict[str, str], var: str) -> str:
    """Run an extracted model-id block with a fixed environment and read `var`."""
    assignments = "\n".join(f"{key}={value!r}" for key, value in sorted(env.items()))
    script = f"set -euo pipefail\n{assignments}\n{block}\nprintf '%s\\n' \"${{{var}}}\"\n"
    result = subprocess.run(
        [bash, "-s"], input=script, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_compose_uses_ods_entrypoint() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "PERPLEXICA_SCRAPE_URL_MAX_CHARS=${PERPLEXICA_SCRAPE_URL_MAX_CHARS:-30000}" in compose
    assert "/app/ods-entrypoint.sh" in compose
    assert "./extensions/services/perplexica/docker-entrypoint.sh:/app/ods-entrypoint.sh:ro" in compose
    assert 'exec /bin/sh /app/ods-entrypoint.sh \\"$@\\"' in compose
    assert "OPENAI_BASE_URL=${HERMES_LLM_BASE_URL:-${LLM_API_URL:-http://llama-server:8080}/v1}" in compose
    assert "OPENAI_API_KEY=${HERMES_LLM_API_KEY:-${LITELLM_KEY:-${OPENAI_API_KEY:-no-key}}}" in compose
    assert "LEMONADE_MODEL=${LEMONADE_MODEL:-}" in compose
    assert "sync-model-config.js:/app/ods-sync-model-config.js:ro" in compose
    assert "sync-search-config.js:/app/ods-sync-search-config.js:ro" in compose
    assert "SEARXNG_API_URL=http://searxng:8080" in compose
    assert "PERPLEXICA_SEARXNG_API_URL=${PERPLEXICA_SEARXNG_API_URL:-}" in compose


def test_search_adapter_config_and_secret_contracts() -> None:
    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    env_vars = {
        item["key"]: item
        for item in manifest["service"]["env_vars"]
    }
    adapter = env_vars["PERPLEXICA_SEARXNG_API_URL"]
    assert adapter["required"] is False
    assert adapter["secret"] is False
    assert adapter["default"] == ""

    brave_manifest = yaml.safe_load(
        (BRAVE_DIR / "manifest.yaml").read_text(encoding="utf-8")
    )
    brave_env = {
        item["key"]: item
        for item in brave_manifest["service"]["env_vars"]
    }
    assert brave_env["BRAVE_SEARCH_API_KEY"]["secret"] is True
    assert brave_env["BRAVE_SEARCH_SEARXNG_COMPAT"]["default"] == "0"

    brave_compose = (BRAVE_DIR / "compose.yaml").read_text(encoding="utf-8")
    assert "BRAVE_SEARCH_API_KEY=${BRAVE_SEARCH_API_KEY:-}" in brave_compose
    assert "BRAVE_SEARCH_SEARXNG_COMPAT=${BRAVE_SEARCH_SEARXNG_COMPAT:-0}" in brave_compose
    assert "BRAVE_SEARCH_UPSTREAM_URL" not in brave_compose


def test_bind_mounted_entrypoints_do_not_require_executable_bit() -> None:
    service_entrypoints = (
        (COMPOSE, "/app/ods-entrypoint.sh", 'exec /bin/sh /app/ods-entrypoint.sh \\"$@\\"'),
        (WHISPER_COMPOSE, "/app/docker-entrypoint.sh", "exec /bin/sh /app/docker-entrypoint.sh"),
    )
    for compose_path, mounted_script, shell_exec in service_entrypoints:
        compose = compose_path.read_text(encoding="utf-8")
        assert f"until [ -f {mounted_script} ]" in compose
        assert f"until [ -x {mounted_script} ]" not in compose
        assert shell_exec in compose
        assert f"exec {mounted_script}" not in compose


def _scrape_patch_program() -> str:
    """Return the Node program the entrypoint runs against each bundle file."""
    lines = ENTRYPOINT.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line.rstrip().endswith("<<'NODE'"))
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == "NODE")
    return "\n".join(lines[start + 1:end]) + "\n"


def _run_scrape_patch(node: str, tmp: Path, bundle: str, max_chars: int = 30000) -> tuple[int, str]:
    program = tmp / "patch.js"
    program.write_text(_scrape_patch_program(), encoding="utf-8")
    chunk = tmp / "641.js"
    chunk.write_text(bundle, encoding="utf-8")
    result = subprocess.run(
        [node, str(program), str(chunk), str(max_chars)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode, chunk.read_text(encoding="utf-8")


def test_entrypoint_patches_scrape_url_result_content() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert "name:\"scrape_url\"" in script
    assert "PERPLEXICA_SCRAPE_URL_MAX_CHARS" in script
    # Vane 1.12.2 moved the app root from /home/perplexica to /home/vane.
    assert 'for app_root in "$PWD" /home/vane /home/perplexica; do' in script
    assert 'search_root="/home/perplexica/.next/server"' not in script


# The scrape_url action objects from the minified .next/server/chunks/641.js of
# the pinned itzcrazykns1337/vane:slim-v1.12.2 image and of the previous
# itzcrazykns1337/perplexica:slim-latest pin, exactly as bundled except for the
# shortened tool-description string. Vane source: v1.12.2
# src/lib/agents/search/researcher/actions/scrapeURL.ts (`enabled: (_) => true`).
VANE_SCRAPE_ACTION = (
    "{name:\"scrape_url\",schema:i,getToolDescription:()=>\"Scrape the provided URLs.\",getDescript"
    "ion:()=>j,enabled:a=>!0,execute:async(a,b)=>{a.urls=a.urls.slice(0,3);let c=crypto.randomU"
    "UID(),d=!1,i=b.session.getBlock(b.researchBlockId),j=[];return await Promise.all(a.urls.ma"
    "p(async a=>{try{let k=await e.A.scrape(a);if(!d&&i&&\"research\"===i.type)d=!0,i.data.subSte"
    "ps.push({id:c,type:\"reading\",reading:[{content:\"\",metadata:{url:a,title:k.title}}]}),b.ses"
    "sion.updateBlock(b.researchBlockId,[{op:\"replace\",path:\"/data/subSteps\",value:i.data.subSt"
    "eps}]);else if(d&&i&&\"research\"===i.type){let d=i.data.subSteps.findIndex(a=>a.id===c);i.d"
    "ata.subSteps[d].reading.push({content:\"\",metadata:{url:a,title:k.title}}),b.session.update"
    "Block(b.researchBlockId,[{op:\"replace\",path:\"/data/subSteps\",value:i.data.subSteps}])}let "
    "l=(0,f.A)(k.content,4e3,500),m=\"\";if(l.length>1)try{await Promise.all(l.map(async a=>{let "
    "c=await b.llm.generateObject({messages:[{role:\"system\",content:g},{role:\"user\",content:`<q"
    "ueries>Summarize</queries>\n<scraped_data>${a}</scraped_data>`}],schema:h});m+=c.extracted_"
    "facts+\"\\n\"}))}catch(a){console.log(\"Error during extraction, falling back to raw content\","
    "a),m=l[0]}else m=k.content;j.push({content:m,metadata:{url:a,title:k.title}})}catch(b){j.p"
    "ush({content:`Failed to fetch content from ${a}: ${b}`,metadata:{url:a,title:`Error scrapi"
    "ng ${a}`}})}})),{type:\"search_results\",results:j}}}"
)
LEGACY_SCRAPE_ACTION = (
    "{name:\"scrape_url\",schema:f,getToolDescription:()=>\"Scrape the provided URLs.\",getDescript"
    "ion:()=>g,enabled:a=>!0,execute:async(a,b)=>{a.urls=a.urls.slice(0,3);let c=crypto.randomU"
    "UID(),d=!1,f=b.session.getBlock(b.researchBlockId),g=[];return await Promise.all(a.urls.ma"
    "p(async a=>{try{let h=await fetch(a),i=await h.text(),j=i.match(/<title>(.*?)<\\/title>/i)?"
    ".[1]||`Content from ${a}`;if(!d&&f&&\"research\"===f.type)d=!0,f.data.subSteps.push({id:c,ty"
    "pe:\"reading\",reading:[{content:\"\",metadata:{url:a,title:j}}]}),b.session.updateBlock(b.res"
    "earchBlockId,[{op:\"replace\",path:\"/data/subSteps\",value:f.data.subSteps}]);else if(d&&f&&\""
    "research\"===f.type){let d=f.data.subSteps.findIndex(a=>a.id===c);f.data.subSteps[d].readin"
    "g.push({content:\"\",metadata:{url:a,title:j}}),b.session.updateBlock(b.researchBlockId,[{op"
    ":\"replace\",path:\"/data/subSteps\",value:f.data.subSteps}])}let k=e.turndown(i);g.push({cont"
    "ent:k,metadata:{url:a,title:j}})}catch(b){g.push({content:`Failed to fetch content from ${"
    "a}: ${b}`,metadata:{url:a,title:`Error fetching ${a}`}})}})),{type:\"search_results\",result"
    "s:g}}}"
)

# Evaluates an action object with the bundle's free names stubbed. scrape()
# (Vane) and fetch() (legacy) record each URL the action tries to open.
_SCRAPE_ACTION_HARNESS = """
const fs = require("fs");
const calls = [];
var e = {A: {scrape: async (url) => { calls.push(url); throw new Error("blocked"); }}, turndown: (html) => String(html)};
var f = {A: (text) => [text]}, g = "", h = {}, i = {}, j = "";
globalThis.fetch = async (url) => { calls.push(url); throw new Error("blocked"); };
const action = eval("(" + fs.readFileSync(process.argv[2], "utf8") + ")");
(async () => {
  const result = await action.execute(
    {urls: ["http://169.254.169.254/latest/meta-data", "http://litellm:4000/v1/models"]},
    {session: {getBlock: () => undefined, updateBlock() {}}, researchBlockId: "r", llm: {}});
  process.stdout.write(JSON.stringify({
    offered: ["speed", "balanced", "quality"].map((mode) => action.enabled({mode, sources: ["web"]})),
    calls, results: result.results.length}));
})();
"""


def _run_scrape_action(node: str, tmp: Path, action: str) -> dict:
    source = tmp / "action.js"
    source.write_text(action, encoding="utf-8")
    harness = tmp / "harness.js"
    harness.write_text(_SCRAPE_ACTION_HARNESS, encoding="utf-8")
    result = subprocess.run(
        [node, str(harness), str(source)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_scrape_patch_disables_and_caps_legacy_and_vane_bundles_idempotently() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    cases = (
        (LEGACY_SCRAPE_ACTION, 'g.push({content:k.slice(0,30000),metadata:{url:a,title:j}})'),
        (VANE_SCRAPE_ACTION, 'j.push({content:m.slice(0,30000),metadata:{url:a,title:k.title}})'),
    )
    for action, expected in cases:
        with tempfile.TemporaryDirectory(prefix="ods-perplexica-patch-") as temp_dir:
            tmp = Path(temp_dir)
            bundle = f"let k={action},l=1;"
            code, patched = _run_scrape_patch(node, tmp, bundle)
            assert code == 0
            assert "enabled:a=>!1,execute:async(a,b)=>{a.urls=[];" in patched
            assert "enabled:a=>!0" not in patched
            assert expected in patched
            assert patched.count(".slice(0,30000)") == 1
            # Only the two patched sites change.
            assert patched == bundle.replace("enabled:a=>!0", "enabled:a=>!1").replace(
                "a.urls=a.urls.slice(0,3);", "a.urls=[];"
            ).replace(expected.replace(".slice(0,30000)", ""), expected)
            # The reading-progress push has a literal empty content and must
            # stay untouched.
            if action is VANE_SCRAPE_ACTION:
                assert 'reading.push({content:"",metadata:{url:a,title:k.title}})' in patched

            # `docker restart` reuses the patched layer; the second start must
            # recognize any minified variable name rather than fail closed.
            code, repatched = _run_scrape_patch(node, tmp, patched)
            assert code == 0
            assert repatched == patched


def test_patched_scrape_url_is_never_offered_and_opens_no_url() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    for action in (VANE_SCRAPE_ACTION, LEGACY_SCRAPE_ACTION):
        with tempfile.TemporaryDirectory(prefix="ods-perplexica-patch-") as temp_dir:
            tmp = Path(temp_dir)
            # Upstream: offered in every mode, and opens internal addresses.
            upstream = _run_scrape_action(node, tmp, action)
            assert upstream["offered"] == [True, True, True]
            assert upstream["calls"] == [
                "http://169.254.169.254/latest/meta-data", "http://litellm:4000/v1/models"
            ]
            code, patched = _run_scrape_patch(node, tmp, action)
            assert code == 0
            # Patched: never offered, and a model that names it anyway opens
            # nothing (ActionRegistry.executeAll does not check enabled()).
            disabled = _run_scrape_action(node, tmp, patched)
            assert disabled == {"offered": [False, False, False], "calls": [], "results": 0}


def test_scrape_patch_fails_closed_on_unknown_shapes() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    with tempfile.TemporaryDirectory(prefix="ods-perplexica-patch-") as temp_dir:
        tmp = Path(temp_dir)
        # scrape_url that cannot be disabled: the container must not start.
        unknown = 'name:"scrape_url",unknownShape()'
        code, unchanged = _run_scrape_patch(node, tmp, unknown)
        assert (code, unchanged) == (3, unknown)
        enabled_elsewhere = VANE_SCRAPE_ACTION.replace(
            "a.urls=a.urls.slice(0,3);", "a.urls=a.urls.slice(0,5);"
        )
        code, unchanged = _run_scrape_patch(node, tmp, enabled_elsewhere)
        assert (code, unchanged) == (3, enabled_elsewhere)
        # Disabled, but the result push site is unknown.
        no_push = VANE_SCRAPE_ACTION.replace("j.push({content:m,", "j.unshift({content:m,")
        code, unchanged = _run_scrape_patch(node, tmp, no_push)
        assert (code, unchanged) == (2, no_push)


def test_entrypoint_reports_a_failed_disable_and_stops() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert "could not disable it" in script
    assert 'if [ "$status" -eq 3 ]; then' in script
    patch_block = script[script.index("patch_scrape_url() {"):script.index("\npatch_scrape_url\n")]
    assert patch_block.index("could not disable it") < patch_block.index("return 1", patch_block.index("could not disable it"))
    # set -eu: a failed patch stops the entrypoint before the app starts.
    assert script.splitlines().count("patch_scrape_url") == 1
    assert script.index("\npatch_scrape_url\n") < script.index('exec docker-entrypoint.sh "$@"')
    assert "set -eu" in script


def test_release_pin_is_consistent_across_surfaces() -> None:
    release = json.loads(RELEASE.read_text(encoding="utf-8"))
    image = release["image"]
    assert re.fullmatch(
        r"itzcrazykns1337/vane:slim-v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}", image
    ), image
    assert release["tag"] == f"v{release['version']}"
    assert f"slim-{release['tag']}@" in image
    assert re.fullmatch(r"[0-9a-f]{40}", release["sourceCommit"])
    assert set(release["platformManifests"]) == {"linux/amd64", "linux/arm64"}
    for digest in release["platformManifests"].values():
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", digest), digest

    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert compose["services"]["perplexica"]["image"] == image

    lock = json.loads(DEPENDENCY_LOCK.read_text(encoding="utf-8"))
    pin = next(entry for entry in lock["entries"] if entry.get("id") == "perplexica.app")
    assert pin["value"] == image

    assert f'PULL_LIST+=("{image}|' in IMAGES_PHASE.read_text(encoding="utf-8")


def test_compose_mounts_state_under_the_pinned_app_root() -> None:
    release = json.loads(RELEASE.read_text(encoding="utf-8"))
    app_root = release["appRoot"]
    volumes = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]["perplexica"]["volumes"]
    # The named volumes keep their ODS names, so an upgrade remounts the same
    # settings, chat history and uploads at the renamed image's app root.
    assert f"perplexica-data:{app_root}/data" in volumes
    assert f"perplexica-uploads:{app_root}/uploads" in volumes
    assert not any("/home/perplexica/" in volume for volume in volumes)


def test_env_schema_allows_scrape_cap_override() -> None:
    schema = json.loads(ENV_SCHEMA.read_text(encoding="utf-8"))
    property_schema = schema["properties"]["PERPLEXICA_SCRAPE_URL_MAX_CHARS"]
    assert property_schema["type"] == "integer"
    assert property_schema["default"] == 30000
    assert property_schema["minimum"] == 1000


def test_compose_restores_image_command() -> None:
    # Setting `entrypoint:` in compose drops the upstream image's CMD
    # (`node server.js`). The override must restate it or the patched
    # entrypoint exits 0 with no app process, restart-looping.
    compose = COMPOSE.read_text(encoding="utf-8")
    assert 'command: ["node", "server.js"]' in compose


def test_entrypoint_falls_back_to_node_server_when_no_args() -> None:
    # Belt-and-suspenders: even if a future compose change drops `command:`,
    # the entrypoint should still launch the app instead of exiting 0.
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'if [ "$#" -eq 0 ]' in script
    assert "set -- node server.js" in script


def test_entrypoint_reconciles_persisted_model_route_on_every_start() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    compose = COMPOSE.read_text(encoding="utf-8")
    sync_script = SYNC_SCRIPT.read_text(encoding="utf-8")
    assert "sync_model_route" in script
    assert "node /app/ods-sync-model-config.js" in script
    assert "PERPLEXICA_MODEL_SYNC_ATTEMPTS" in script
    assert "ODS_MODEL_SWITCHBOARD" in compose
    assert 'switchboardMode === "enabled"' in sync_script


def test_entrypoint_reconciles_explicit_search_route_independently() -> None:
    script = ENTRYPOINT.read_text(encoding="utf-8")
    assert "sync_search_route" in script
    assert "node /app/ods-sync-search-config.js" in script
    assert "PERPLEXICA_SEARCH_SYNC_ATTEMPTS" in script


def test_sync_script_persists_exact_lemonade_route() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [{"key": "old", "name": "old"}],
            "config": {"baseURL": "http://old/v1", "apiKey": "old-key"},
        }],
        "preferences": {
            "defaultChatModel": "old",
            "defaultChatProvider": "openai-provider",
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODE": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "Modern-Model",
            "GGUF_FILE": "Modern-Model.gguf",
            "OPENAI_BASE_URL": "http://litellm:4000/v1",
            "OPENAI_API_KEY": "litellm-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "Modern-Model"
    provider = state["modelProviders"][0]
    assert provider["chatModels"] == [{"key": "Modern-Model", "name": "Modern-Model"}]
    assert provider["config"] == {
        "baseURL": "http://litellm:4000/v1",
        "apiKey": "litellm-key",
    }
    assert state["preferences"]["defaultChatModel"] == "Modern-Model"


def test_sync_script_uses_stable_alias_when_switchboard_enabled() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [{"key": "Qwen3.5-2B-Q4_K_M", "name": "Qwen3.5-2B-Q4_K_M"}],
            "config": {"baseURL": "http://litellm:4000/v1", "apiKey": "old-key"},
        }],
        "preferences": {
            "defaultChatModel": "Qwen3.5-2B-Q4_K_M",
            "defaultChatProvider": "openai-provider",
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODEL_SWITCHBOARD": "enabled",
            "ODS_MODE": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "Qwen3.5-2B-Q4_K_M",
            "GGUF_FILE": "Qwen3.5-2B-Q4_K_M.gguf",
            "OPENAI_BASE_URL": "http://litellm:4000",
            "OPENAI_API_KEY": "litellm-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ods/current"
    provider = state["modelProviders"][0]
    assert provider["chatModels"] == [{"key": "ods/current", "name": "ods/current"}]
    assert provider["config"] == {
        "baseURL": "http://litellm:4000/v1",
        "apiKey": "litellm-key",
    }
    assert state["preferences"]["defaultChatModel"] == "ods/current"


def test_sync_script_falls_back_to_extra_gguf_when_exact_lemonade_id_is_absent() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [],
            "config": {},
        }],
        "preferences": {},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODE": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "",
            "GGUF_FILE": "Modern-Model.gguf",
            "OPENAI_BASE_URL": "http://litellm:4000/v1",
            "OPENAI_API_KEY": "litellm-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "extra.Modern-Model.gguf"
    assert state["modelProviders"][0]["chatModels"] == [{
        "key": "extra.Modern-Model.gguf",
        "name": "extra.Modern-Model.gguf",
    }]


def test_sync_script_normalizes_base_url_without_v1_suffix() -> None:
    node = _node_cmd_or_skip()
    if node is None:
        return

    state = {
        "modelProviders": [{
            "id": "openai-provider",
            "type": "openai",
            "chatModels": [],
            "config": {},
        }],
        "preferences": {},
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            state[payload["key"]] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "ODS_MODE": "local",
            "GGUF_FILE": "Modern-Model.gguf",
            "OPENAI_BASE_URL": "http://custom-litellm:4000/",
            "OPENAI_API_KEY": "custom-key",
        })
        result = subprocess.run(
            [node, str(SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.returncode == 0, result.stderr
    assert state["modelProviders"][0]["config"] == {
        "baseURL": "http://custom-litellm:4000/v1",
        "apiKey": "custom-key",
    }


def _run_search_route_sync(
    endpoint: str,
    current_endpoint: str = "http://searxng:8080",
) -> tuple[subprocess.CompletedProcess[str], dict, list[dict]]:
    node = _node_cmd_or_skip()
    if node is None:
        raise RuntimeError("Node.js is required")

    state = {
        "modelProviders": [],
        "preferences": {},
        "search": {"searxngURL": current_endpoint},
    }
    writes: list[dict] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"values": state}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            writes.append(payload)
            if payload["key"] == "search.searxngURL":
                state["search"]["searxngURL"] = payload["value"]
            body = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        env = os.environ.copy()
        env.update({
            "PERPLEXICA_CONFIG_URL": f"http://127.0.0.1:{server.server_port}/api/config",
            "PERPLEXICA_SEARXNG_API_URL": endpoint,
            "OPENAI_BASE_URL": "",
            "GGUF_FILE": "",
            "LLM_MODEL": "",
            "LEMONADE_MODEL": "",
            "ODS_MODEL_SWITCHBOARD": "",
            "ODS_MODE": "",
            "AMD_INFERENCE_RUNTIME": "",
            "LLM_BACKEND": "",
            "BRAVE_SEARCH_API_KEY": "must-not-enter-perplexica-config",
        })
        result = subprocess.run(
            [node, str(SEARCH_SYNC_SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return result, state, writes


def test_explicit_search_adapter_updates_persisted_install() -> None:
    result, state, writes = _run_search_route_sync("http://brave-search:8585/")

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "http://brave-search:8585"
    assert state["search"]["searxngURL"] == "http://brave-search:8585"
    assert writes == [{
        "key": "search.searxngURL",
        "value": "http://brave-search:8585",
    }]
    assert "must-not-enter-perplexica-config" not in json.dumps(writes)


def test_empty_search_adapter_preserves_existing_searxng_setting() -> None:
    result, state, writes = _run_search_route_sync("")

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert state["search"]["searxngURL"] == "http://searxng:8080"
    assert writes == []


def test_search_adapter_sync_is_idempotent() -> None:
    result, state, writes = _run_search_route_sync(
        "http://brave-search:8585",
        current_endpoint="http://brave-search:8585",
    )

    assert result.returncode == 0, result.stderr
    assert state["search"]["searxngURL"] == "http://brave-search:8585"
    assert writes == []


def test_invalid_search_adapter_fails_closed_without_mutating_config() -> None:
    result, state, writes = _run_search_route_sync(
        "https://user:password@example.com/search?token=secret"
    )

    assert result.returncode == 1
    assert "PERPLEXICA_SEARXNG_API_URL" in result.stderr
    assert state["search"]["searxngURL"] == "http://searxng:8080"
    assert writes == []


# The model id Perplexica must end up with is decided in five places: the
# container-side sync script, scripts/bootstrap-upgrade.sh, the seeding step in
# phase 12, the post-install validation in phase 13, and the repair script. The
# last three used to read `${LLM_BACKEND:-${AMD_INFERENCE_RUNTIME:-}}`, which
# can never see AMD_INFERENCE_RUNTIME because phase 06 always writes a
# non-empty LLM_BACKEND.
# The matrix below pins the resolution rule for every runtime combination the
# installer can produce.
_MODEL_ID_CASES = (
    (
        "generic_external_uses_the_pinned_provider_model",
        {
            "EXTERNAL_LLM_URL": "http://host.docker.internal:8080",
            "EXTERNAL_LLM_MODEL": "Qwen3.5-9B-Q4_K_M.gguf",
            "GGUF_FILE": "stale-local-tier.gguf",
            "LLM_BACKEND": "external",
            "AMD_INFERENCE_RUNTIME": "",
            "LEMONADE_MODEL": "",
        },
        "Qwen3.5-9B-Q4_K_M.gguf",
    ),
    (
        "stale_external_model_without_a_route_is_ignored",
        {
            "EXTERNAL_LLM_URL": "",
            "EXTERNAL_LLM_MODEL": "stale-external-model",
            "GGUF_FILE": "active-local-tier.gguf",
            "LLM_BACKEND": "llama-server",
            "AMD_INFERENCE_RUNTIME": "",
            "LEMONADE_MODEL": "",
        },
        "active-local-tier.gguf",
    ),
    (
        "amd_local_runs_lemonade_under_llama_server_backend",
        {
            "GGUF_FILE": "Modern-Model.gguf",
            "LLM_BACKEND": "llama-server",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "",
        },
        "extra.Modern-Model.gguf",
    ),
    (
        "external_lemonade_uses_the_discovered_model_id",
        {
            "GGUF_FILE": "Modern-Model.gguf",
            "LLM_BACKEND": "lemonade",
            "AMD_INFERENCE_RUNTIME": "lemonade",
            "LEMONADE_MODEL": "Qwen3-8B-GGUF",
        },
        "Qwen3-8B-GGUF",
    ),
    (
        "llama_server_backends_use_the_bare_gguf_id",
        {
            "GGUF_FILE": "Modern-Model.gguf",
            "LLM_BACKEND": "llama-server",
            "AMD_INFERENCE_RUNTIME": "",
            "LEMONADE_MODEL": "",
        },
        "Modern-Model.gguf",
    ),
)


def test_health_phase_seeds_the_same_model_id_as_the_sync_script() -> None:
    bash = _bash_cmd_or_skip()
    if bash is None:
        return

    block = _slice_block(
        HEALTH_PHASE,
        'PERPLEXICA_MODEL="${LLM_MODEL:-default}"',
        "    fi",
    )
    for name, env, expected in _MODEL_ID_CASES:
        resolved = _resolve_expected_model(
            bash, block, {"LLM_MODEL": "qwen3-30b-a3b", **env}, "PERPLEXICA_MODEL"
        )
        assert resolved == expected, f"{name}: expected {expected}, got {resolved}"


def test_perplexica_compose_passes_the_external_model_to_runtime_sync() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    environment = compose["services"]["perplexica"]["environment"]
    assert "EXTERNAL_LLM_URL=${EXTERNAL_LLM_URL:-}" in environment
    assert "EXTERNAL_LLM_MODEL=${EXTERNAL_LLM_MODEL:-}" in environment


def test_runtime_sync_prefers_the_external_model_over_stale_local_tier_metadata() -> None:
    source = SYNC_SCRIPT.read_text(encoding="utf-8")
    assert 'const externalUrl = String(process.env.EXTERNAL_LLM_URL || "").trim();' in source
    assert 'const externalModel = externalUrl' in source
    assert re.search(r'switchboardMode === "enabled"\s*\? "ods/current"\s*:\s*externalModel\s*\? externalModel', source)


def test_post_install_validation_resolves_the_same_model_id_as_the_sync_script() -> None:
    bash = _bash_cmd_or_skip()
    if bash is None:
        return

    block = _slice_block(
        SUMMARY_PHASE,
        '_perplexica_model="${LLM_MODEL:-qwen3-30b-a3b}"',
        "        fi",
    )
    for name, env, expected in _MODEL_ID_CASES:
        resolved = _resolve_expected_model(
            bash, block, {"LLM_MODEL": "qwen3-30b-a3b", **env}, "_perplexica_model"
        )
        assert resolved == expected, f"{name}: expected {expected}, got {resolved}"


def test_repair_script_resolves_the_same_model_id_as_the_sync_script() -> None:
    bash = _bash_cmd_or_skip()
    if bash is None:
        return

    block = _slice_block(REPAIR_SCRIPT, 'if [[ -z "$PERPLEXICA_MODEL" ]]; then', "fi")
    for name, env, expected in _MODEL_ID_CASES:
        resolved = _resolve_expected_model(
            bash,
            block,
            {"LLM_MODEL": "qwen3-30b-a3b", "PERPLEXICA_MODEL": "", **env},
            "PERPLEXICA_MODEL",
        )
        assert resolved == expected, f"{name}: expected {expected}, got {resolved}"


if __name__ == "__main__":
    test_compose_uses_ods_entrypoint()
    test_search_adapter_config_and_secret_contracts()
    test_bind_mounted_entrypoints_do_not_require_executable_bit()
    test_entrypoint_patches_scrape_url_result_content()
    test_scrape_patch_disables_and_caps_legacy_and_vane_bundles_idempotently()
    test_patched_scrape_url_is_never_offered_and_opens_no_url()
    test_scrape_patch_fails_closed_on_unknown_shapes()
    test_entrypoint_reports_a_failed_disable_and_stops()
    test_release_pin_is_consistent_across_surfaces()
    test_compose_mounts_state_under_the_pinned_app_root()
    test_env_schema_allows_scrape_cap_override()
    test_compose_restores_image_command()
    test_entrypoint_falls_back_to_node_server_when_no_args()
    test_entrypoint_reconciles_persisted_model_route_on_every_start()
    test_entrypoint_reconciles_explicit_search_route_independently()
    test_sync_script_persists_exact_lemonade_route()
    test_sync_script_uses_stable_alias_when_switchboard_enabled()
    test_sync_script_falls_back_to_extra_gguf_when_exact_lemonade_id_is_absent()
    test_sync_script_normalizes_base_url_without_v1_suffix()
    test_explicit_search_adapter_updates_persisted_install()
    test_empty_search_adapter_preserves_existing_searxng_setting()
    test_search_adapter_sync_is_idempotent()
    test_invalid_search_adapter_fails_closed_without_mutating_config()
    test_health_phase_seeds_the_same_model_id_as_the_sync_script()
    test_perplexica_compose_passes_the_external_model_to_runtime_sync()
    test_runtime_sync_prefers_the_external_model_over_stale_local_tier_metadata()
    test_post_install_validation_resolves_the_same_model_id_as_the_sync_script()
    test_repair_script_resolves_the_same_model_id_as_the_sync_script()
