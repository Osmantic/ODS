"""Contract tests for scripts/register-model-store.py.

Registration writes the model-store registry and a generated read-only
Compose overlay for dashboard-api. These tests pin the identity, custody, and
cross-check rules: store-id charset, duplicate directory/id conflicts, the
16-store cap, qualified-profile verification (checkpoint must live directly
inside the registered directory, backend must be explicit, MTP only when the
qualified runtime proved it), and atomic non-symlink writes.
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "register-model-store.py"
SPEC = importlib.util.spec_from_file_location("register_model_store", MODULE)
rms = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rms)


@pytest.fixture
def install(tmp_path):
    root = tmp_path / "install"
    root.mkdir()
    (root / "data").mkdir()
    return root


@pytest.fixture
def models(tmp_path):
    directory = tmp_path / "models"
    directory.mkdir()
    (directory / "model-a.gguf").write_bytes(b"gguf-bytes")
    return directory


def register(install, models, identifier="extra", **kw):
    return rms.register(install, identifier, models, **kw)


def qualified_profile(tmp_path, models, runtime=None, mtp=True, **kw):
    runtime = runtime or (tmp_path / "llama-server")
    runtime.write_bytes(b"binary")
    os.chmod(runtime, 0o755)
    profile = {
        "schemaVersion": 1,
        "baselineCommand": [str(runtime), "--model",
                            str(models / "model-a.gguf")],
        "runtime": {"mtp": mtp},
        "profile": {"context": 32768, "draftTokens": 8,
                    "modelSha256": "a" * 64, "runtimeSha256": "b" * 64,
                    "loadModeArguments": ["--flash-attn"]},
        "signature": "sig",
    }
    profile.update(kw)
    path = tmp_path / "qualified.json"
    path.write_text(json.dumps(profile))
    return path


class TestRegistryBasics:
    def test_registers_new_store(self, install, models):
        result = register(install, models)
        assert result["registered"] == "extra"
        assert result["requiresApiRecreate"] is True
        doc = json.loads((install / "data/model-stores.json").read_text())
        assert doc["stores"][0]["containerPath"] == "/model-stores/extra"
        overlay = json.loads(
            (install / ".model-stores.compose.json").read_text())
        vol = overlay["services"]["dashboard-api"]["volumes"][0]
        assert vol["read_only"] is True
        assert vol["bind"]["create_host_path"] is False

    def test_registry_created_under_data(self, install, models):
        register(install, models)
        assert (install / "data/model-stores.json").is_file()

    def test_missing_install_dir_rejected(self, tmp_path, models):
        with pytest.raises(FileNotFoundError):
            register(tmp_path / "absent", models)

    def test_missing_model_dir_rejected(self, install, tmp_path):
        with pytest.raises(FileNotFoundError):
            register(install, tmp_path / "absent")

    def test_model_dir_must_be_directory(self, install, models):
        file = models / "model-a.gguf"
        with pytest.raises(ValueError, match="required"):
            register(install, file)

    @pytest.mark.parametrize("identifier", [
        "default", "Bad", "x" * 49, "a_b", "-lead", "a" * 47 + "!",
    ])
    def test_identifier_charset(self, install, models, identifier):
        with pytest.raises(ValueError, match="required"):
            register(install, models, identifier)

    def test_registry_symlink_rejected(self, install, models):
        target = install / "data/real.json"
        target.write_text('{"schemaVersion":1,"stores":[]}')
        (install / "data/model-stores.json").symlink_to(target)
        with pytest.raises(ValueError, match="Unsafe"):
            register(install, models)

    def test_registry_oversize_rejected(self, install, models):
        big = install / "data/model-stores.json"
        big.write_text(" " * (rms.MAX_REGISTRY_BYTES + 1))
        with pytest.raises(ValueError, match="Unsafe"):
            register(install, models)

    def test_registry_schema_rejected(self, install, models):
        (install / "data/model-stores.json").write_text(
            '{"schemaVersion":2,"stores":[]}')
        with pytest.raises(ValueError, match="Invalid"):
            register(install, models)

    def test_sixteen_store_cap(self, install, models, tmp_path):
        stores = [{"id": f"s{i}", "hostPath": str(tmp_path / f"m{i}"),
                   "containerPath": f"/model-stores/s{i}", "profiles": {}}
                  for i in range(16)]
        (install / "data/model-stores.json").write_text(
            json.dumps({"schemaVersion": 1, "stores": stores}))
        with pytest.raises(ValueError, match="full"):
            register(install, models)

    def test_same_directory_other_id_rejected(self, install, models):
        register(install, models, "first")
        with pytest.raises(ValueError, match="different store id"):
            register(install, models, "second")

    def test_same_id_other_directory_rejected(self, install, models,
                                              tmp_path):
        register(install, models, "extra")
        other = tmp_path / "other"
        other.mkdir()
        with pytest.raises(ValueError, match="another directory"):
            register(install, other, "extra")

    def test_reregister_same_pair_is_idempotent(self, install, models):
        register(install, models, "extra")
        result = register(install, models, "extra")
        doc = json.loads((install / "data/model-stores.json").read_text())
        assert len(doc["stores"]) == 1 and result["registered"] == "extra"


class TestQualifiedProfile:
    def test_profile_attached(self, install, models, tmp_path):
        profile = qualified_profile(tmp_path, models)
        result = register(install, models, qualified=profile,
                          backend="vulkan", enable_mtp=True)
        entry = json.loads(
            (install / "data/model-stores.json").read_text())["stores"][0]
        p = entry["profiles"]["model-a.gguf"]
        assert p["backend"] == "vulkan" and p["mtp"] is True
        assert p["contextLength"] == 32768 and p["draftTokens"] == 8

    @pytest.mark.parametrize("backend", [None, "opencl", "webgpu"])
    def test_backend_allowlist(self, install, models, tmp_path, backend):
        profile = qualified_profile(tmp_path, models)
        with pytest.raises(ValueError, match="backend"):
            register(install, models, qualified=profile, backend=backend)

    def test_profile_schema_required(self, install, models, tmp_path):
        profile = qualified_profile(tmp_path, models, schemaVersion=2)
        with pytest.raises(ValueError, match="qualify-mtp"):
            register(install, models, qualified=profile, backend="cpu")

    def test_baseline_command_shape(self, install, models, tmp_path):
        profile = tmp_path / "qualified.json"
        profile.write_text(json.dumps({
            "schemaVersion": 1, "baselineCommand": ["only-one"],
            "runtime": {"mtp": True}, "profile": {}, "signature": "s"}))
        with pytest.raises(ValueError, match="qualify-mtp"):
            register(install, models, qualified=profile, backend="cpu")

    def test_checkpoint_must_live_inside_store(self, install, models,
                                               tmp_path):
        outside = tmp_path / "other"
        outside.mkdir()
        checkpoint = outside / "model-a.gguf"
        checkpoint.write_bytes(b"x")
        runtime = tmp_path / "llama-server"
        runtime.write_bytes(b"b")
        profile = tmp_path / "qualified.json"
        profile.write_text(json.dumps({
            "schemaVersion": 1,
            "baselineCommand": [str(runtime), "--model", str(checkpoint)],
            "runtime": {"mtp": True},
            "profile": {"context": 1, "draftTokens": 1, "modelSha256": "a" * 64,
                        "runtimeSha256": "b" * 64},
            "signature": "s"}))
        with pytest.raises(ValueError, match="directly inside"):
            register(install, models, qualified=profile, backend="cpu")

    def test_mtp_requires_qualified_runtime(self, install, models, tmp_path):
        profile = qualified_profile(tmp_path, models, mtp=False)
        with pytest.raises(ValueError, match="MTP"):
            register(install, models, qualified=profile, backend="cpu",
                     enable_mtp=True)

    def test_old_profiles_preserved_on_reregister(self, install, models,
                                                  tmp_path):
        register(install, models, "extra",
                 qualified=qualified_profile(tmp_path, models),
                 backend="cpu")
        # Plain re-register keeps the recorded profile.
        register(install, models, "extra")
        entry = json.loads(
            (install / "data/model-stores.json").read_text())["stores"][0]
        assert "model-a.gguf" in entry["profiles"]


class TestAtomicJson:
    def test_symlink_target_refused(self, install):
        target = install / "link.json"
        real = install / "real.json"
        real.write_text("{}")
        target.symlink_to(real)
        with pytest.raises(ValueError, match="symlink"):
            rms.atomic_json(target, {"a": 1})

    def test_write_and_cleanup(self, install):
        target = install / "doc.json"
        rms.atomic_json(target, {"a": 1})
        assert json.loads(target.read_text()) == {"a": 1}
        assert not list(install.glob("doc.json.*.tmp"))
