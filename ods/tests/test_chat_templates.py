"""Chat template overrides (any-model WP5, TEST-PLAN T-U-5): index, vendored files, CI check.

Covers bin/model_profile/templates.py, scripts/validate-chat-templates.py and
scripts/vendor-chat-templates.py with a temporary chat-templates directory
holding one small fake template. Nothing here reaches the network.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

from model_profile import templates  # noqa: E402


def _load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_script("validate-chat-templates")
vendoring = _load_script("vendor-chat-templates")

FAKE_TEMPLATE = b"{% for message in messages %}<|{{ message.role }}|>{{ message.content }}\n{% endfor %}"
FAKE_SHA = hashlib.sha256(FAKE_TEMPLATE).hexdigest()
EMBEDDED_SHA = "a" * 64
LICENSE_TEXT = b"MIT License\n\nCopyright (c) 2023-2026 The ggml authors\n"


def _entry(**changes):
    entry = {
        "id": "fake-tools-fix",
        "embeddedTemplateSha256": EMBEDDED_SHA,
        "file": "upstream-b9014/Fake-Model.jinja",
        "fileSha256": FAKE_SHA,
        "builds": ["b9014", "b11429"],
        "reason": "The model's own template drops tool calls.",
    }
    entry.update(changes)
    return entry


def _tree(tmp_path, entries=None, manifest=None):
    root = tmp_path / "chat-templates"
    vendored = root / "upstream-b9014"
    vendored.mkdir(parents=True)
    (vendored / "LICENSE").write_bytes(LICENSE_TEXT)
    (vendored / "Fake-Model.jinja").write_bytes(FAKE_TEMPLATE)
    listed = {"Fake-Model.jinja": FAKE_SHA} if manifest is None else manifest
    (vendored / "SHA256SUMS").write_bytes(templates.render_manifest(listed).encode("utf-8"))
    document = {"schemaVersion": 1, "overrides": [_entry()] if entries is None else entries}
    (root / "index.json").write_text(json.dumps(document), encoding="utf-8")
    return root


def _write_index(root, entries):
    (root / "index.json").write_text(json.dumps({"schemaVersion": 1, "overrides": entries}), encoding="utf-8")


# --- a valid tree --------------------------------------------------------------

def test_a_valid_tree_passes_and_the_cli_says_so(tmp_path, capsys):
    root = _tree(tmp_path)

    assert templates.tree_errors(root) == []
    assert validator.main(["--root", str(root)]) == 0
    assert "[PASS]" in capsys.readouterr().out


def test_the_committed_tree_is_valid_and_vendors_only_what_the_index_uses():
    root = ROOT / "config" / "chat-templates"

    assert templates.tree_errors(root) == []
    used = {entry["file"] for entry in templates.load_index(root)}
    for directory in root.glob("upstream-*"):
        listed = templates.parse_manifest((directory / "SHA256SUMS").read_text(encoding="utf-8"))
        assert {f"{directory.name}/{name}" for name in listed} <= used
        assert (directory / "LICENSE").read_bytes().startswith(b"MIT License")


# --- manifest and license (T-U-5 item 2) ----------------------------------------

def test_a_changed_template_fails_its_manifest_hash(tmp_path, capsys):
    root = _tree(tmp_path)
    (root / "upstream-b9014" / "Fake-Model.jinja").write_bytes(FAKE_TEMPLATE + b" ")

    errors = templates.tree_errors(root)

    assert any("does not match its SHA-256 in SHA256SUMS" in error for error in errors)
    assert validator.main(["--root", str(root)]) == 1
    assert "[FAIL]" in capsys.readouterr().out


def test_the_license_must_be_present_and_mit(tmp_path):
    root = _tree(tmp_path)
    (root / "upstream-b9014" / "LICENSE").unlink()
    assert any("LICENSE is missing" in error for error in templates.tree_errors(root))

    (root / "upstream-b9014" / "LICENSE").write_bytes(b"All rights reserved\n")
    assert any("not llama.cpp's MIT license" in error for error in templates.tree_errors(root))


def test_files_outside_the_manifest_or_the_layout_fail(tmp_path):
    root = _tree(tmp_path)
    (root / "upstream-b9014" / "Unlisted.jinja").write_bytes(b"{{ x }}")
    (root / "notes.txt").write_text("stray", encoding="utf-8")
    (root / "upstream-latest").mkdir()

    errors = templates.tree_errors(root)

    assert any("Unlisted.jinja is not listed in SHA256SUMS" in error for error in errors)
    assert any(error.startswith("notes.txt does not belong here") for error in errors)
    assert any(error.startswith("upstream-latest does not belong here") for error in errors)


def test_a_manifest_line_for_a_missing_file_or_a_bad_line_fails(tmp_path):
    root = _tree(tmp_path, manifest={"Fake-Model.jinja": FAKE_SHA, "Gone.jinja": "b" * 64})
    assert any("lists Gone.jinja, which is missing" in error for error in templates.tree_errors(root))

    (root / "upstream-b9014" / "SHA256SUMS").write_text(f"{FAKE_SHA} Fake-Model.jinja\n", encoding="utf-8")
    assert any("line 1 is not" in error for error in templates.tree_errors(root))


def test_only_templates_the_index_uses_may_be_vendored(tmp_path):
    root = _tree(tmp_path, entries=[])

    errors = templates.tree_errors(root)

    assert errors == ["upstream-b9014/Fake-Model.jinja is vendored but no index entry uses it; "
                      "vendor only the templates the index references"]


# --- index entries (T-U-5 items 1 and 6) ----------------------------------------

@pytest.mark.parametrize("changes, message", [
    ({"id": "Fake_Fix"}, ".id must be"),
    ({"embeddedTemplateSha256": "A" * 64}, ".embeddedTemplateSha256 must be 64 lower-case hex"),
    ({"embeddedTemplateSha256": "a" * 63}, ".embeddedTemplateSha256 must be 64 lower-case hex"),
    ({"fileSha256": "not-a-hash"}, ".fileSha256 must be 64 lower-case hex"),
    ({"file": "../outside.jinja"}, ".file must be upstream-b<build>/<name>.jinja"),
    ({"file": "/etc/passwd"}, ".file must be upstream-b<build>/<name>.jinja"),
    ({"file": "upstream-b9014\\Fake-Model.jinja"}, ".file must be upstream-b<build>/<name>.jinja"),
    ({"file": "upstream-b9014/../../x.jinja"}, ".file must be upstream-b<build>/<name>.jinja"),
    ({"builds": []}, ".builds must list"),
    ({"builds": ["9014"]}, ".builds must list"),
    ({"builds": ["b9014", "b9014"]}, ".builds must list"),
    ({"reason": ""}, ".reason must be one line"),
    ({"reason": "x" * 201}, ".reason must be one line"),
])
def test_malformed_entries_are_refused(tmp_path, changes, message):
    root = _tree(tmp_path, entries=[_entry(**changes)])

    errors = templates.tree_errors(root)

    assert any(message in error for error in errors), errors
    with pytest.raises(templates.TemplateIndexError):
        templates.load_index(root)


def test_entries_need_exactly_the_documented_fields(tmp_path):
    entry = _entry()
    entry["url"] = "https://example.test/template.jinja"
    root = _tree(tmp_path, entries=[entry])

    assert any("must have exactly the fields" in error for error in templates.tree_errors(root))


def test_ids_are_unique_and_one_template_has_one_fix_per_build(tmp_path):
    root = _tree(tmp_path, entries=[_entry(), _entry()])
    errors = templates.tree_errors(root)
    assert "override id fake-tools-fix is used twice" in errors

    _write_index(root, [_entry(), _entry(id="other-fix", builds=["b9014"])])
    assert any("both fix the same template on b9014" in error for error in templates.tree_errors(root))


def test_an_entry_hash_must_be_the_manifest_hash(tmp_path):
    root = _tree(tmp_path, entries=[_entry(fileSha256="c" * 64)])

    assert any("fileSha256 is not the SHA-256" in error for error in templates.tree_errors(root))


def test_an_entry_file_must_be_vendored(tmp_path):
    root = _tree(tmp_path, entries=[_entry(), _entry(id="second", embeddedTemplateSha256="d" * 64,
                                                     file="upstream-b9014/Other.jinja")])

    assert any("upstream-b9014/Other.jinja is not vendored" in error for error in templates.tree_errors(root))


def test_the_index_document_shape_is_fixed(tmp_path):
    root = _tree(tmp_path)
    (root / "index.json").write_text(json.dumps({"schemaVersion": 2, "overrides": []}), encoding="utf-8")
    assert templates.tree_errors(root)[0] == "index.json schemaVersion must be 1"
    (root / "index.json").write_text("{not json", encoding="utf-8")
    assert templates.tree_errors(root)[0].startswith("index.json is not JSON")
    (root / "index.json").unlink()
    assert "index.json is missing" in templates.tree_errors(root)


# --- matching: exact hash and qualified build only (T-U-5 item 1) ---------------

def test_match_needs_the_exact_hash_and_a_qualified_build():
    entries = [_entry()]

    assert templates.match(entries, EMBEDDED_SHA, "b9014-ad4b5c5f")["id"] == "fake-tools-fix"
    assert templates.match(entries, EMBEDDED_SHA, "b11429-d81235049")["id"] == "fake-tools-fix"
    assert templates.match(entries, "a" * 63 + "b", "b9014-ad4b5c5f") is None
    assert templates.match(entries, EMBEDDED_SHA.upper(), "b9014-ad4b5c5f") is None
    assert templates.match(entries, EMBEDDED_SHA[:-1], "b9014-ad4b5c5f") is None
    assert templates.match(entries, EMBEDDED_SHA, "b8210-1234567") is None
    assert templates.match(entries, EMBEDDED_SHA, "b90140-1234567") is None
    assert templates.match(entries, EMBEDDED_SHA, None) is None
    assert templates.match(entries, None, "b9014-ad4b5c5f") is None


@pytest.mark.parametrize("build_info, tag", [
    ("b9014-ad4b5c5f", "b9014"), ("b11429", "b11429"), ("unknown", None), ("", None), (None, None), (9014, None),
])
def test_build_tags_come_from_llama_cpp_build_info(build_info, tag):
    assert templates.build_tag(build_info) == tag


def test_a_template_is_used_only_when_its_bytes_match(tmp_path):
    root = _tree(tmp_path)
    entry = templates.load_index(root)[0]

    assert templates.template_path(root, entry) == root / "upstream-b9014" / "Fake-Model.jinja"
    (root / "upstream-b9014" / "Fake-Model.jinja").write_bytes(b"{{ changed }}")
    with pytest.raises(templates.TemplateIndexError, match="does not match its recorded SHA-256"):
        templates.template_path(root, entry)
    (root / "upstream-b9014" / "Fake-Model.jinja").unlink()
    with pytest.raises(templates.TemplateIndexError, match="missing"):
        templates.template_path(root, entry)


# --- nothing at runtime reaches the network (T-U-5 item 5) ----------------------

def test_the_runtime_module_has_no_network_access():
    source = (ROOT / "bin" / "model_profile" / "templates.py").read_text(encoding="utf-8")

    assert not re.search(r"^\s*(?:import|from)\s+(?:urllib|http|socket|ssl|requests|httpx)\b", source, re.M)


# --- vendoring script ----------------------------------------------------------

def test_vendoring_writes_exact_bytes_the_license_and_the_manifest(tmp_path):
    requested = []
    upstream = {
        "https://raw.githubusercontent.com/ggml-org/llama.cpp/b9014/LICENSE": LICENSE_TEXT,
        "https://raw.githubusercontent.com/ggml-org/llama.cpp/b9014/models/templates/Fake-Model.jinja": FAKE_TEMPLATE,
        "https://raw.githubusercontent.com/ggml-org/llama.cpp/b9014/models/templates/Second.jinja": b"{{ x }}\r\n",
    }

    def fetch(url):
        requested.append(url)
        return upstream[url]

    root = tmp_path / "chat-templates"
    assert vendoring.vendor(root, "b9014", ["Fake-Model.jinja"], fetch=fetch) == {"Fake-Model.jinja": FAKE_SHA}
    vendoring.vendor(root, "b9014", ["Second.jinja"], fetch=fetch)

    directory = root / "upstream-b9014"
    assert requested.count("https://raw.githubusercontent.com/ggml-org/llama.cpp/b9014/LICENSE") == 1
    assert (directory / "Second.jinja").read_bytes() == b"{{ x }}\r\n"
    assert templates.parse_manifest((directory / "SHA256SUMS").read_text(encoding="utf-8")) == {
        "Fake-Model.jinja": FAKE_SHA,
        "Second.jinja": hashlib.sha256(b"{{ x }}\r\n").hexdigest(),
    }
    _write_index(root, [_entry(), _entry(id="second", embeddedTemplateSha256="e" * 64,
                                         file="upstream-b9014/Second.jinja",
                                         fileSha256=hashlib.sha256(b"{{ x }}\r\n").hexdigest())])
    assert templates.tree_errors(root) == []


@pytest.mark.parametrize("tag, name", [
    ("main", "Fake-Model.jinja"), ("b9014", "../Fake-Model.jinja"), ("b9014", "Fake-Model.txt"),
    ("b9014", "sub/Fake-Model.jinja"),
])
def test_vendoring_refuses_other_tags_and_names(tmp_path, tag, name):
    with pytest.raises(ValueError):
        vendoring.vendor(tmp_path, tag, [name], fetch=lambda url: pytest.fail(f"fetched {url}"))
    assert list(tmp_path.iterdir()) == []


# --- CI wiring (T-U-5 item 6) ---------------------------------------------------

def test_validate_catalog_runs_the_check_on_template_changes():
    workflow = (ROOT.parent / ".github" / "workflows" / "validate-catalog.yml").read_text(encoding="utf-8")

    assert "python ods/scripts/validate-chat-templates.py" in workflow
    assert "python -m pytest -q ods/tests/test_chat_templates.py" in workflow
    for path in ("ods/config/chat-templates/**", "ods/scripts/validate-chat-templates.py",
                 "ods/scripts/vendor-chat-templates.py", "ods/bin/model_profile/templates.py"):
        assert workflow.count(f"'{path}'") == 2, path


# --- native macOS restarts (scripts/resolve-model-store.py) ---------------------

resolver = _load_script("resolve-model-store")


def _install_with_template(tmp_path, override):
    install = tmp_path / "install"
    (install / "config").mkdir(parents=True)
    _tree(install / "config")
    env = "GGUF_FILE=model.gguf\n" + (f"MODEL_CHAT_TEMPLATE_OVERRIDE={override}\n" if override else "")
    (install / ".env").write_text(env, encoding="utf-8")
    return install


def test_a_restart_gets_the_fixed_template_the_switch_chose(tmp_path):
    install = _install_with_template(tmp_path, "fake-tools-fix")
    path = Path(resolver.chat_template_path(install))
    assert path == install / "config" / "chat-templates" / "upstream-b9014" / "Fake-Model.jinja"


def test_a_restart_without_an_override_uses_the_models_own_template(tmp_path):
    assert resolver.chat_template_path(_install_with_template(tmp_path, None)) is None


def test_a_missing_or_altered_fixed_template_refuses_the_restart(tmp_path):
    install = _install_with_template(tmp_path, "no-such-fix")
    with pytest.raises(ValueError, match="not in this installation"):
        resolver.chat_template_path(install)
    install = _install_with_template(tmp_path / "altered", "fake-tools-fix")
    (install / "config" / "chat-templates" / "upstream-b9014" / "Fake-Model.jinja").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256"):
        resolver.chat_template_path(install)
