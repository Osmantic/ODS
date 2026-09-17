"""The settings writer must remain readable by every direct API file reader."""

import pytest

from env_values import quote_env_value


@pytest.mark.parametrize("value", ["it's $5 \"q\" back\\slash", "  model #1  ", "ordinary"])
def test_persisted_readers_decode_writer_output(tmp_path, monkeypatch, value):
    import config
    import gpu
    import helpers
    import performance_oracle
    from routers import models, updates

    env_path = tmp_path / ".env"
    env_path.write_text(
        "".join(f"{key}={quote_env_value(value)}\n" for key in ["LLM_MODEL", "GGUF_FILE", "ODS_VERSION"])
        + 'CTX_SIZE="8192" # context limit\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(helpers, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(models, "_ENV_PATH", env_path)
    monkeypatch.setattr(updates, "INSTALL_DIR", str(tmp_path))
    monkeypatch.setenv("ODS_INSTALL_DIR", str(tmp_path))
    assert config._find_env_file_value("LLM_MODEL") == (True, value)
    assert gpu._read_env_var_from_file_state("LLM_MODEL") == (True, value)
    assert performance_oracle.read_env_file_value("LLM_MODEL", tmp_path) == value
    assert performance_oracle.read_context_length(tmp_path) == 8192
    assert models._read_active_model() == value
    assert updates._read_current_version() == value
    assert helpers.get_model_info().name == value


def test_process_environment_is_not_reparsed_as_dotenv(tmp_path, monkeypatch):
    import performance_oracle

    monkeypatch.setenv("ODS_READER_TEST", "literal # part $value")
    assert performance_oracle.read_env_value("ODS_READER_TEST", tmp_path) == "literal # part $value"
