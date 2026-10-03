"""Reject expensive manifests before constructors expand YAML aliases."""

from pathlib import Path

import pytest

import config


@pytest.mark.parametrize("suffix,text", [
    ("yaml", "value: " + "[" * 2000 + "0" + "]" * 2000),
    ("json", '{"value":' + "[" * 2000 + "0" + "]" * 2000 + "}"),
])
def test_deep_documents_fail_as_manifest_errors(tmp_path, suffix, text):
    path = tmp_path / ("manifest." + suffix)
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="nesting exceeds"):
        config._read_manifest_file(path)


@pytest.mark.parametrize("merge", [False, True])
def test_alias_expansion_is_rejected_before_construction(tmp_path, monkeypatch, merge):
    text = "n0: &n0 {value: leaf}\n" if merge else "n0: &n0 [leaf]\n"
    for index in range(1, 20):
        aliases = f"[*n{index - 1}, *n{index - 1}]"
        value = "{<<: " + aliases + "}" if merge else aliases
        text += f"n{index}: &n{index} {value}\n"
    path = tmp_path / "manifest.yaml"
    path.write_text(text, encoding="utf-8")

    def forbidden(*args):
        pytest.fail("constructors must not run for an oversized alias graph")

    monkeypatch.setattr(config._ManifestLoader, "construct_document", forbidden)
    with pytest.raises(ValueError, match="expansion exceeds"):
        config._read_manifest_file(path)


def test_cycles_are_rejected_before_construction(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text("value: &loop [*loop]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cyclic"):
        config._read_manifest_file(path)


def test_normal_aliases_and_merge_overrides_remain_supported(tmp_path):
    path = tmp_path / "manifest.yaml"
    path.write_text("defaults: &defaults {port: 8080, name: café}\n"
                    "service: {<<: *defaults, port: 9000}\n"
                    "copy: *defaults\n", encoding="utf-8")
    data = config._read_manifest_file(path)
    assert data["service"] == {"port": 9000, "name": "café"}
    assert data["copy"] == data["defaults"]


def test_manifest_read_has_a_byte_limit(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_bytes(b" " * (config.MAX_MANIFEST_BYTES + 1))
    with pytest.raises(ValueError, match="size limit"):
        config._read_manifest_file(path)


def test_all_bundled_manifests_fit_bounds():
    services = Path(config.__file__).resolve().parent.parent
    extensions = services.parent
    paths = [*extensions.rglob("manifest.yaml"), *extensions.rglob("manifest.yml"),
             *extensions.rglob("manifest.json")]
    assert paths
    assert services / "pixel-agent" / "manifest.yaml" in paths
    for path in paths:
        assert isinstance(config._read_manifest_file(path), dict), path
