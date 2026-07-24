"""Unit tests for the shared ``env_file.read_env_file_value`` helper."""

from env_file import read_env_file_value


def _write_env(tmp_path, text: str):
    (tmp_path / ".env").write_text(text, encoding="utf-8")
    return tmp_path


def test_reads_plain_value(tmp_path):
    _write_env(tmp_path, "LLM_MODEL=qwen3\nCTX_SIZE=8192\n")

    assert read_env_file_value("LLM_MODEL", tmp_path) == "qwen3"
    assert read_env_file_value("CTX_SIZE", tmp_path) == "8192"


def test_splits_on_first_equals_only(tmp_path):
    _write_env(tmp_path, "GPU_ASSIGNMENT_JSON_B64=eyJhPTEifQ==\n")

    assert read_env_file_value("GPU_ASSIGNMENT_JSON_B64", tmp_path) == "eyJhPTEifQ=="


def test_strips_surrounding_quotes(tmp_path):
    _write_env(tmp_path, 'A="double"\nB=\'single\'\n')

    assert read_env_file_value("A", tmp_path) == "double"
    assert read_env_file_value("B", tmp_path) == "single"


def test_key_prefix_does_not_collide(tmp_path):
    # "CTX" must not match the "CTX_SIZE=" line — the '=' delimiter guards it.
    _write_env(tmp_path, "CTX_SIZE=4096\n")

    assert read_env_file_value("CTX", tmp_path) == ""


def test_returns_first_matching_line(tmp_path):
    _write_env(tmp_path, "K=first\nK=second\n")

    assert read_env_file_value("K", tmp_path) == "first"


def test_missing_key_returns_empty(tmp_path):
    _write_env(tmp_path, "OTHER=1\n")

    assert read_env_file_value("NOPE", tmp_path) == ""


def test_missing_file_returns_empty(tmp_path):
    assert read_env_file_value("ANY", tmp_path) == ""


def test_accepts_str_install_dir(tmp_path):
    _write_env(tmp_path, "PORT=9000\n")

    assert read_env_file_value("PORT", str(tmp_path)) == "9000"
