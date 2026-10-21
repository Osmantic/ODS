from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "bin" / "ods-host-agent.py").read_text(encoding="utf-8")


def test_log_handlers_spool_subprocess_output_before_bounded_read():
    assert SOURCE.count("tempfile.SpooledTemporaryFile(max_size=65536)") >= 2
    assert SOURCE.count("stdout=log_buffer, stderr=log_buffer") >= 2
    assert SOURCE.count("log_buffer.read()[-50000:]") >= 2
    assert "capture_output=True" not in SOURCE[SOURCE.index("def _handle_logs"):SOURCE.index("def _handle_service_logs")]

