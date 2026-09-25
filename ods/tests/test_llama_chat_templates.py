"""ODS llama.cpp chat templates (config/llama-server/templates).

Qwen3.5's chat template drops the (empty) think block from every assistant
turn before the latest user message. The server generated those turns after
`<think>\n\n</think>\n\n`, so each owner turn rewrote the previous run and a
local llama.cpp server re-read all of it. ODS ships each Qwen3.5 GGUF's own
template with exactly one line changed to Qwen3.6's `preserve_thinking`
switch, which Pixel sends; every other client renders byte-identically.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "config" / "llama-server" / "templates"
CATALOG = ROOT / "config" / "model-library.json"
ORIGINAL = "        {%- if loop.index0 > ns.last_query_index %}\n"
# Verbatim from the Qwen3.6 GGUF templates (unsloth/Qwen3.6-27B-GGUF, -35B-A3B-GGUF).
PRESERVE = ("        {%- if (preserve_thinking is defined and preserve_thinking is true) "
            "or (loop.index0 > ns.last_query_index) %}\n")
# SHA-256 of the templates embedded in the catalog's unsloth Qwen3.5 GGUFs, as
# llama.cpp b9014 reports them in /props.
SHIPPED = {
    # Qwen3.5-27B, -35B-A3B and -122B-A10B (tower1 /props, HF gguf metadata).
    "qwen3.5-preserve-thinking.jinja": "e60df41481b6ad20571cda7e2e1290cfee25670f1b50973f74576c9354c66336",
    # Qwen3.5-2B, -4B and -9B: these default to non-thinking when the switch is absent.
    "qwen3.5-small-preserve-thinking.jinja": "7f0e529032c25183bcd66c7f238da2d377f43be754a94e2725a58c4e16d2ed67",
}
SMALL = {"Qwen3.5-2B-Q4_K_M.gguf", "Qwen3.5-4B-Q4_K_M.gguf", "Qwen3.5-9B-Q4_K_M.gguf"}
MOUNT = "./config/llama-server/templates:/config/llama-server/templates:ro,z"


def test_shipped_templates_are_the_embedded_template_plus_one_switch():
    assert sorted(path.name for path in TEMPLATES.glob("*.jinja")) == sorted(SHIPPED)
    for name, embedded_sha in SHIPPED.items():
        text = (TEMPLATES / name).read_bytes().decode("utf-8")
        assert text.count(PRESERVE) == 1 and ORIGINAL not in text, name
        original = text.replace(PRESERVE, ORIGINAL)
        assert hashlib.sha256(original.encode("utf-8")).hexdigest() == embedded_sha, name


def test_catalog_maps_every_qwen35_gguf_to_its_template_variant():
    models = json.loads(CATALOG.read_text(encoding="utf-8"))["models"]
    mapped = {model["gguf_file"]: model["llama_chat_template"] for model in models if "llama_chat_template" in model}
    qwen35 = {model["gguf_file"] for model in models if str(model.get("gguf_file", "")).startswith("Qwen3.5-")}
    assert set(mapped) == qwen35 and qwen35
    for gguf, template in mapped.items():
        expected = "qwen3.5-small-preserve-thinking.jinja" if gguf in SMALL else "qwen3.5-preserve-thinking.jinja"
        assert template == expected, gguf


@pytest.mark.parametrize(("overlay", "wired"), [
    ("docker-compose.nvidia.yml", True), ("docker-compose.cpu.yml", True),
    ("docker-compose.base.yml", False), ("docker-compose.amd.yml", False),
    ("docker-compose.intel.yml", False), ("docker-compose.arc.yml", False), ("docker-compose.apple.yml", False),
])
def test_only_the_pinned_llama_cpp_overlays_read_the_template(overlay, wired):
    service = (yaml.safe_load((ROOT / overlay).read_text(encoding="utf-8"))["services"].get("llama-server") or {})
    environment = service.get("environment") or []
    volumes = service.get("volumes") or []
    # Pass-through only: an unset key must stay unset, never become an empty path.
    assert ("LLAMA_ARG_CHAT_TEMPLATE_FILE" in environment) is wired
    assert not any(str(item).startswith("LLAMA_ARG_CHAT_TEMPLATE_FILE=") for item in environment)
    assert (MOUNT in volumes) is wired


def _selector(*args):
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "select-model.py"), "--catalog", str(CATALOG),
                             "--installable-only", "--env", *args], capture_output=True, text=True, check=True)
    return dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)


def test_selector_writes_the_template_for_llama_cpp_backends_only():
    nvidia = _selector("--backend", "nvidia", "--vram-mb", "32768", "--ram-gb", "64", "--tier", "3",
                       "--max-size-mb", "17000", "--host-arch", "x86_64")
    assert nvidia["GGUF_FILE"] == '"Qwen3.5-27B-Q4_K_M.gguf"'
    assert nvidia["LLAMA_ARG_CHAT_TEMPLATE_FILE"] == '"/config/llama-server/templates/qwen3.5-preserve-thinking.jinja"'
    apple = _selector("--backend", "apple", "--memory-type", "unified", "--ram-gb", "16", "--tier", "APPLE")
    assert apple["GGUF_FILE"] == '"Qwen3.5-9B-Q4_K_M.gguf"'
    assert apple["LLAMA_ARG_CHAT_TEMPLATE_FILE"] == '"/config/llama-server/templates/qwen3.5-small-preserve-thinking.jinja"'
    amd = _selector("--backend", "amd", "--memory-type", "unified", "--ram-gb", "128", "--tier", "3")
    assert "LLAMA_ARG_CHAT_TEMPLATE_FILE" not in amd


def test_preserved_model_rederives_its_template_from_the_catalog(tmp_path):
    spec = importlib.util.spec_from_file_location("preserve_active_model", ROOT / "scripts" / "preserve-active-model.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = {"llama_chat_template": "qwen3.5-preserve-thinking.jinja"}
    expected = "/config/llama-server/templates/qwen3.5-preserve-thinking.jinja"
    assert module.catalog_chat_template(model, "nvidia", CATALOG) == expected
    assert module.catalog_chat_template(model, "amd", CATALOG) == ""
    assert module.catalog_chat_template({}, "nvidia", CATALOG) == ""
    assert module.catalog_chat_template(model, "nvidia", tmp_path / "model-library.json") == ""


# --- Rendering (Jinja2 stands in for llama.cpp's engine here; the pinned
# llama.cpp engine renders the same fixture in test-pixel-tool-grammar.yml).

TOOLS = [{"type": "function", "function": {"name": "write", "description": "Write a file.",
          "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]


def _conversation(owner_turns):
    messages = [{"role": "system", "content": "You are Pixel."}]
    for index in range(owner_turns):
        messages += [
            {"role": "user", "content": f"Owner message {index}"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"type": "function", "function": {"name": "write", "arguments": {"path": f"f{index}.txt"}}}]},
            {"role": "tool", "content": f"wrote f{index}.txt"},
            {"role": "assistant", "content": f"Done {index}."},
        ]
    return messages


def _render(template, messages, **kwargs):
    jinja2 = pytest.importorskip("jinja2")
    environment = jinja2.Environment(extensions=["jinja2.ext.loopcontrols"])
    environment.filters["tojson"] = lambda value, **_: json.dumps(value)

    def raise_exception(message):
        raise ValueError(message)

    return environment.from_string(template).render(messages=messages, tools=TOOLS, add_generation_prompt=True,
                                                    raise_exception=raise_exception, **kwargs)


@pytest.mark.parametrize("name", sorted(SHIPPED))
def test_preserve_thinking_makes_the_next_owner_turn_append_only(name):
    template = (TEMPLATES / name).read_text(encoding="utf-8")
    embedded = template.replace(PRESERVE, ORIGINAL)
    first = _conversation(1)
    second = _conversation(1)[:-1]
    generated = "Done 0."
    second = [*second, {"role": "assistant", "content": generated}, {"role": "user", "content": "Owner message 1"}]
    for enable_thinking in (False, True):
        # Without the switch the shipped file renders exactly like the GGUF's own template.
        for messages in (first, second):
            assert _render(template, messages, enable_thinking=enable_thinking) == \
                _render(embedded, messages, enable_thinking=enable_thinking)
    # The slot after the first owner turn: its final request plus the generated answer.
    slot = _render(template, first[:-1], enable_thinking=False, preserve_thinking=True) + generated
    assert _render(template, second, enable_thinking=False, preserve_thinking=True).startswith(slot)
    stock = _render(embedded, second, enable_thinking=False)
    assert not stock.startswith(slot)
    assert stock.startswith(slot[:slot.index("<think>\n\n</think>\n\n<tool_call>")])
