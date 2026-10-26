"""ComfyUI extension contracts that hold without Docker or a GPU.

The NVIDIA image build is reproducible and fails loudly; Open WebUI's image
settings are owner-settable with defaults that match what ODS ships; and the
image model name agrees everywhere it is written down.
"""
import json
import re
from pathlib import Path

import yaml

ODS = Path(__file__).resolve().parents[1]
COMFYUI = ODS / "extensions/services/comfyui"
DOCKERFILE = (COMFYUI / "Dockerfile").read_text(encoding="utf-8")
DOCKERFILE_CODE = "\n".join(line for line in DOCKERFILE.splitlines()
                            if not line.lstrip().startswith("#"))
MODEL = "sdxl_lightning_4step.safetensors"

# Open WebUI settings an owner may set in .env, with the shipped defaults.
OPEN_WEBUI_IMAGE_DEFAULTS = {
    "COMFYUI_BASE_URL": "http://comfyui:8188",
    "IMAGE_GENERATION_MODEL": MODEL,
    "IMAGE_SIZE": "1024x1024",
    "IMAGE_STEPS": "4",
}


def _open_webui_environment():
    base = yaml.safe_load((ODS / "docker-compose.base.yml").read_text(encoding="utf-8"))
    return base["services"]["open-webui"]["environment"]


def test_torch_pins_match_the_build_assertion():
    installed = dict(re.findall(r"\b(torch|torchvision|torchaudio)==([0-9.]+)", DOCKERFILE_CODE))
    asserted = dict(re.findall(r"(torch|torchvision|torchaudio)\.__version__ == '([0-9.]+)\+cu128'",
                               DOCKERFILE_CODE))
    assert set(installed) == {"torch", "torchvision", "torchaudio"}, installed
    assert installed == asserted
    assert "--index-url https://download.pytorch.org/whl/cu128" in DOCKERFILE_CODE


def test_build_steps_do_not_hide_failures():
    assert "|| true" not in DOCKERFILE_CODE
    assert "2>/dev/null" not in DOCKERFILE_CODE


def test_custom_nodes_are_pinned_and_manager_is_absent():
    clones = re.findall(r"git clone (?:--branch \S+ --depth 1 )?https://github.com/([^/\s]+/[^\s.]+)",
                        DOCKERFILE_CODE)
    assert "comfyanonymous/ComfyUI" in clones
    nodes = [repo for repo in clones if repo != "comfyanonymous/ComfyUI"]
    assert nodes == ["city96/ComfyUI-GGUF", "kijai/ComfyUI-KJNodes",
                     "Kosinkadink/ComfyUI-VideoHelperSuite", "Lightricks/ComfyUI-LTXVideo"]
    assert len(re.findall(r"git checkout [0-9a-f]{7,40}\b", DOCKERFILE_CODE)) == len(nodes)
    assert "ComfyUI-Manager" not in DOCKERFILE_CODE


def test_build_imports_every_custom_node():
    gate = re.search(r"python3 main\.py --cpu --quick-test-for-ci.*?rm -rf /tmp/ods-quick-test",
                     DOCKERFILE_CODE, re.S)
    assert gate, "the import gate is missing"
    step = gate.group(0)
    assert "--database-url sqlite:///:memory:" in step
    assert '! grep -E "IMPORT FAILED|PRESTARTUP FAILED"' in step
    assert 'test ! -e "$COMFYUI_DIR/user"' in step
    # Built-in nodes list these when they load (nodes_audio.py reads input/),
    # so each must exist before ComfyUI starts.
    created = re.search(r"mkdir -p ((?:/tmp/ods-quick-test/\w+\s*\\?\s*)+)", DOCKERFILE_CODE)
    assert created, "the scratch directories are not created"
    for name in ("user", "output", "input", "temp"):
        assert f"/tmp/ods-quick-test/{name}" in created.group(1), name
        assert f"--{name}-directory /tmp/ods-quick-test/{name}" in step, name


def test_open_webui_image_settings_are_owner_settable():
    environment = _open_webui_environment()
    schema = json.loads((ODS / ".env.schema.json").read_text(encoding="utf-8"))["properties"]
    example = (ODS / ".env.example").read_text(encoding="utf-8")
    for key, default in OPEN_WEBUI_IMAGE_DEFAULTS.items():
        assert environment[key] == f"${{{key}:-{default}}}", key
        assert str(schema[key]["default"]) == default, key
        assert f"# {key}={default}\n" in example, key


def test_image_model_name_agrees_everywhere():
    environment = _open_webui_environment()
    workflow = json.loads(environment["COMFYUI_WORKFLOW"])
    loaders = [node for node in workflow.values() if node["class_type"] == "CheckpointLoaderSimple"]
    assert [node["inputs"]["ckpt_name"] for node in loaders] == [MODEL]

    installer = (ODS / "installers/phases/11-services.sh").read_text(encoding="utf-8")
    assert f'SDXL_MODEL="{MODEL}"' in installer

    n8n = (ODS / "extensions/library/workflows/comfyui/llm-to-image.json").read_text(encoding="utf-8")
    assert f"$env.COMFYUI_MODEL_NAME || '{MODEL}'" in n8n
    assert "flux1-dev" not in n8n


def test_image_feature_metadata_is_truthful():
    manifest = yaml.safe_load((COMFYUI / "manifest.yaml").read_text(encoding="utf-8"))
    feature = next(item for item in manifest["features"] if item["id"] == "images")
    assert "SDXL Lightning" in feature["description"]
    assert "FLUX" not in feature["description"]
    assert feature["requirements"]["vram_gb"] == 8
    assert feature["setup_time"] != "Ready"
