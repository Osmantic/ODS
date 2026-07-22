"""Tests for config.py — manifest loading and service discovery."""

import logging
from pathlib import Path

import pytest

import config
from config import (
    _apply_host_native_llm_service_override,
    _detect_container_default_gateway,
    load_extension_manifests,
    _read_manifest_file,
)


VALID_MANIFEST = """\
schema_version: ods.services.v1
service:
  id: test-service
  name: Test Service
  port: 8080
  health: /health
  gpu_backends: [amd, nvidia]
  external_port_default: 8080
features:
  - id: test-feature
    name: Test Feature
    icon: Zap
    category: inference
    gpu_backends: [amd, nvidia]
"""


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, "unknown"),
        ("", "unknown"),
        (" LOCAL ", "local"),
        ("cloud", "cloud"),
        ("HYBRID", "hybrid"),
        ("lemonade", "lemonade"),
        ("core", "unknown"),
    ],
)
def test_normalize_ods_mode(value, expected):
    assert config.normalize_ods_mode(value) == expected


def test_live_env_value_prefers_last_persisted_value(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "LLM_MODEL=old-model\nLLM_MODEL=new-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("LLM_MODEL", "stale-process-model")

    assert config.read_live_env_value("LLM_MODEL") == "new-model"


def test_live_env_value_preserves_explicit_empty_value(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("LEMONADE_MODEL=\n", encoding="utf-8")
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("LEMONADE_MODEL", "stale-process-model")

    assert config.read_live_env_value("LEMONADE_MODEL", "fallback") == ""


class TestReadManifestFile:

    def test_reads_yaml(self, tmp_path):
        f = tmp_path / "manifest.yaml"
        f.write_text(VALID_MANIFEST)
        data = _read_manifest_file(f)
        assert data["schema_version"] == "ods.services.v1"
        assert data["service"]["id"] == "test-service"

    def test_reads_json(self, tmp_path):
        import json
        f = tmp_path / "manifest.json"
        f.write_text(json.dumps({
            "schema_version": "ods.services.v1",
            "service": {"id": "json-svc", "name": "JSON", "port": 9090},
        }))
        data = _read_manifest_file(f)
        assert data["service"]["id"] == "json-svc"

    def test_rejects_non_dict_root(self, tmp_path):
        f = tmp_path / "manifest.yaml"
        f.write_text("- just\n- a\n- list\n")
        with pytest.raises(ValueError, match="object"):
            _read_manifest_file(f)


class TestHostAgentResolution:

    def test_detect_container_default_gateway_reads_little_endian_route(self, tmp_path):
        route = tmp_path / "route"
        route.write_text(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            "eth0\t00000000\t010012AC\t0003\t0\t0\t0\t00000000\t0\t0\t0\n",
            encoding="utf-8",
        )

        assert _detect_container_default_gateway(str(route)) == "172.18.0.1"

    def test_detect_container_default_gateway_requires_gateway_flag(self, tmp_path):
        route = tmp_path / "route"
        route.write_text(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            "eth0\t00000000\t010012AC\t0001\t0\t0\t0\t00000000\t0\t0\t0\n",
            encoding="utf-8",
        )

        assert _detect_container_default_gateway(str(route)) == ""

    def test_resolve_agent_host_honors_explicit_env(self, monkeypatch):
        monkeypatch.setenv("ODS_AGENT_HOST", "  10.9.8.7  ")
        monkeypatch.setattr(config, "_running_inside_container", lambda: True)
        monkeypatch.setattr(config, "_detect_container_default_gateway", lambda: "172.18.0.1")

        assert config._resolve_agent_host() == "10.9.8.7"

    def test_resolve_agent_host_uses_gateway_inside_container(self, monkeypatch):
        monkeypatch.delenv("ODS_AGENT_HOST", raising=False)
        monkeypatch.setattr(config, "_running_inside_container", lambda: True)
        monkeypatch.setattr(config, "_detect_container_default_gateway", lambda: "172.18.0.1")

        assert config._resolve_agent_host() == "172.18.0.1"

    def test_resolve_agent_host_falls_back_outside_container(self, monkeypatch):
        monkeypatch.delenv("ODS_AGENT_HOST", raising=False)
        monkeypatch.setattr(config, "_running_inside_container", lambda: False)
        monkeypatch.setattr(config, "_detect_container_default_gateway", lambda: "192.168.1.1")

        assert config._resolve_agent_host() == "host.docker.internal"


class TestHostNativeLlmResolution:

    def test_routes_windows_amd_host_runtime_to_ollama_url(self):
        services = {"llama-server": {"host": "llama-server", "port": 8080}}
        env = {
            "AMD_INFERENCE_LOCATION": "host",
            "AMD_INFERENCE_PORT": "9090",
            "OLLAMA_URL": "http://host.docker.internal:9090/v1",
        }

        _apply_host_native_llm_service_override(services, "amd", env)

        assert services["llama-server"]["host"] == "host.docker.internal"
        assert services["llama-server"]["port"] == 9090

    @pytest.mark.parametrize(
        ("backend", "location"),
        [("nvidia", "host"), ("amd", "container"), ("amd", "external")],
    )
    def test_leaves_non_host_native_services_unchanged(self, backend, location):
        services = {"llama-server": {"host": "llama-server", "port": 8080}}

        _apply_host_native_llm_service_override(
            services,
            backend,
            {"AMD_INFERENCE_LOCATION": location, "OLLAMA_URL": "http://host.docker.internal:9090"},
        )

        assert services["llama-server"] == {"host": "llama-server", "port": 8080}


class TestLoadExtensionManifests:

    def test_loads_valid_manifest(self, tmp_path):
        svc_dir = tmp_path / "test-service"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(VALID_MANIFEST)

        services, features, _ = load_extension_manifests(tmp_path, "nvidia")
        assert "test-service" in services
        assert services["test-service"]["port"] == 8080
        assert services["test-service"]["name"] == "Test Service"
        assert services["test-service"]["health"] == "/health"
        assert len(features) == 1
        assert features[0]["id"] == "test-feature"

    def test_normalizes_llm_contract(self, tmp_path):
        svc_dir = tmp_path / "llm-app"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n"
            "  id: llm-app\n"
            "  name: LLM App\n"
            "  port: 8080\n"
            "  health: /health\n"
            "  llm:\n"
            "    consumes: true\n"
            "    route: direct\n"
            "    pinning: none\n"
            "    min_context: 65536\n"
            "    probe:\n"
            "      kind: chat\n"
            "      path: /v1/chat/completions\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "nvidia")

        llm = services["llm-app"]["llm"]
        assert llm["consumes"] is True
        assert llm["route"] == "direct"
        assert llm["pinning"] == "none"
        assert llm["min_context"] == 65536
        assert llm["probe"]["kind"] == "chat"
        assert llm["swap_safe"] is False
        assert llm["badge"] == "not-swap-safe"

    def test_skips_docker_service_when_declared_compose_file_is_absent(self, tmp_path):
        svc_dir = tmp_path / "openclaw"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n"
            "  id: openclaw\n"
            "  name: OpenClaw\n"
            "  type: docker\n"
            "  compose_file: compose.yaml\n"
            "  port: 18789\n"
            "  health: /\n"
            "  llm:\n"
            "    consumes: true\n"
            "    route: direct\n"
            "    pinning: none\n"
            "features:\n"
            "  - id: openclaw-feature\n"
            "    name: OpenClaw Feature\n"
        )

        services, features, _ = load_extension_manifests(tmp_path, "nvidia")

        assert "openclaw" not in services
        assert features == []

    def test_loads_docker_service_when_declared_compose_file_exists(self, tmp_path):
        svc_dir = tmp_path / "openclaw"
        svc_dir.mkdir()
        (svc_dir / "compose.yaml").write_text("services:\n  openclaw:\n    image: test\n")
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n"
            "  id: openclaw\n"
            "  name: OpenClaw\n"
            "  type: docker\n"
            "  compose_file: compose.yaml\n"
            "  port: 18789\n"
            "  health: /\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "nvidia")

        assert "openclaw" in services

    def test_builtin_llm_probe_paths_match_live_service_routes(self):
        services_dir = Path(__file__).resolve().parents[2]

        services, _, _ = load_extension_manifests(services_dir, "nvidia")

        assert services["open-webui"]["llm"]["probe"]["path"] == "/openai/v1/chat/completions"
        assert services["perplexica"]["llm"]["probe"]["path"] == "/api/search"
        assert services["privacy-shield"]["llm"]["probe"]["path"] == "/v1/chat/completions"

    def test_external_port_default_zero_disables_external_port_fallback(self, tmp_path):
        svc_dir = tmp_path / "internal-service"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n"
            "  id: internal-service\n"
            "  name: Internal Service\n"
            "  port: 9119\n"
            "  external_port_default: 0\n"
            "  health: /api/status\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "nvidia")

        assert services["internal-service"]["port"] == 9119
        assert services["internal-service"]["external_port"] == 0

    def test_preserves_host_network_flag(self, tmp_path):
        svc_dir = tmp_path / "host-network-service"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n"
            "  id: host-network-service\n"
            "  name: Host Network Service\n"
            "  host_network: true\n"
            "  port: 0\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "nvidia")

        assert services["host-network-service"]["host_network"] is True

    def test_skips_wrong_schema_version(self, tmp_path):
        svc_dir = tmp_path / "old-service"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v0\nservice:\n  id: old\n  port: 80\n"
        )

        services, features, errors = load_extension_manifests(tmp_path, "nvidia")
        assert len(services) == 0
        assert len(errors) == 1
        assert "Unsupported schema_version" in errors[0]["error"]

    def test_filters_by_gpu_backend(self, tmp_path):
        svc_dir = tmp_path / "nvidia-only"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: nvidia-only\n  name: NVIDIA Only\n  port: 80\n"
            "  gpu_backends: [nvidia]\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "amd")
        assert len(services) == 0

        services, _, _ = load_extension_manifests(tmp_path, "nvidia")
        assert "nvidia-only" in services

    def test_empty_directory(self, tmp_path):
        services, features, _ = load_extension_manifests(tmp_path, "nvidia")
        assert services == {}
        assert features == []

    def test_nonexistent_directory(self, tmp_path):
        missing = tmp_path / "does-not-exist"
        services, features, _ = load_extension_manifests(missing, "nvidia")
        assert services == {}
        assert features == []

    def test_features_filtered_by_gpu(self, tmp_path):
        svc_dir = tmp_path / "mixed"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: mixed\n  name: Mixed\n  port: 80\n"
            "  gpu_backends: [amd, nvidia]\n"
            "features:\n"
            "  - id: amd-feat\n    name: AMD Feature\n    gpu_backends: [amd]\n"
            "  - id: both-feat\n    name: Both Feature\n    gpu_backends: [amd, nvidia]\n"
        )

        _, features, _ = load_extension_manifests(tmp_path, "nvidia")
        feature_ids = [f["id"] for f in features]
        assert "both-feat" in feature_ids
        assert "amd-feat" not in feature_ids

    def test_apple_backend_discovers_services_without_explicit_list(self, tmp_path):
        """Services with no gpu_backends key default to [amd, nvidia, apple]."""
        svc_dir = tmp_path / "generic-svc"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: generic-svc\n  name: Generic\n  port: 80\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "apple")
        assert "generic-svc" in services

    def test_apple_backend_filtered_by_explicit_nvidia_amd_list(self, tmp_path):
        """Docker service explicitly listing [amd, nvidia] is still loaded for apple backend."""
        svc_dir = tmp_path / "gpu-only-svc"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: gpu-only-svc\n  name: GPU Only\n  port: 80\n"
            "  gpu_backends: [amd, nvidia]\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "apple")
        assert "gpu-only-svc" in services

    def test_apple_backend_discovers_service_explicitly_listing_apple(self, tmp_path):
        """Service that lists apple in gpu_backends is discovered for apple backend."""
        svc_dir = tmp_path / "apple-svc"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: apple-svc\n  name: Apple Svc\n  port: 80\n"
            "  gpu_backends: [amd, nvidia, apple]\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "apple")
        assert "apple-svc" in services

    def test_apple_backend_feature_default_discovered(self, tmp_path):
        """Features with no gpu_backends key default to include apple."""
        svc_dir = tmp_path / "svc-with-feature"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: svc\n  name: Svc\n  port: 80\n"
            "features:\n"
            "  - id: default-feat\n    name: Default Feature\n"
        )

        _, features, _ = load_extension_manifests(tmp_path, "apple")
        assert any(f["id"] == "default-feat" for f in features)

    def test_apple_backend_excludes_host_systemd(self, tmp_path):
        """Services with type: host-systemd are excluded on apple backend."""
        svc_dir = tmp_path / "systemd-svc"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: systemd-svc\n  name: Systemd Svc\n  port: 80\n"
            "  type: host-systemd\n"
            "  gpu_backends: [amd, nvidia]\n"
        )

        services, _, _ = load_extension_manifests(tmp_path, "apple")
        assert "systemd-svc" not in services

    def test_apple_backend_loads_all_features(self, tmp_path):
        """Features with gpu_backends: [amd, nvidia] are loaded for apple backend."""
        svc_dir = tmp_path / "svc-with-gpu-feature"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: svc\n  name: Svc\n  port: 80\n"
            "features:\n"
            "  - id: gpu-feat\n    name: GPU Feature\n    gpu_backends: [amd, nvidia]\n"
        )

        _, features, _ = load_extension_manifests(tmp_path, "apple")
        assert any(f["id"] == "gpu-feat" for f in features)

    def test_warns_on_missing_optional_feature_fields(self, tmp_path, caplog):
        """A feature missing optional fields is loaded but a warning is logged."""
        svc_dir = tmp_path / "sparse-svc"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  id: sparse-svc\n  name: Sparse\n  port: 80\n"
            "features:\n"
            "  - id: sparse-feat\n    name: Sparse Feature\n"
        )

        with caplog.at_level(logging.WARNING, logger="config"):
            _, features, _ = load_extension_manifests(tmp_path, "nvidia")

        assert any(f["id"] == "sparse-feat" for f in features)
        warning_msgs = [r.message for r in caplog.records if "missing optional fields" in r.message]
        assert len(warning_msgs) == 1
        assert "sparse-feat" in warning_msgs[0]
        for field in ("description", "icon", "category", "setup_time", "priority"):
            assert field in warning_msgs[0]

    def test_collects_parse_errors(self, tmp_path):
        svc_dir = tmp_path / "broken-svc"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text("invalid: yaml: [unterminated")

        services, features, errors = load_extension_manifests(tmp_path, "nvidia")
        assert len(errors) == 1
        assert "file" in errors[0]
        assert "error" in errors[0]
        assert "broken-svc" in errors[0]["file"]
        assert services == {}
        assert features == []

    def test_collects_error_for_missing_service_id(self, tmp_path):
        """A manifest with a valid schema but no service.id collects an error."""
        svc_dir = tmp_path / "no-id-svc"
        svc_dir.mkdir()
        (svc_dir / "manifest.yaml").write_text(
            "schema_version: ods.services.v1\n"
            "service:\n  name: No ID\n  port: 80\n"
        )

        services, features, errors = load_extension_manifests(tmp_path, "nvidia")
        assert len(errors) == 1
        assert "service.id is required" in errors[0]["error"]
        assert "no-id-svc" in errors[0]["file"]
        assert services == {}
