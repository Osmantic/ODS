"""Unit tests for node capability router version resolution resilience."""

from pathlib import Path
from unittest.mock import patch

from routers.node import _read_ods_version


def test_read_ods_version_from_env(tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("ODS_VERSION=1.5.0\n")
    with patch("routers.node._install_root", return_value=tmp_path):
        ver = _read_ods_version("0.1.0")
        assert ver == "1.5.0"


def test_read_ods_version_from_json_version(tmp_path: Path):
    version_file = tmp_path / ".version"
    version_file.write_text('{"version": "2.0.0"}')
    with patch("routers.node._install_root", return_value=tmp_path):
        ver = _read_ods_version("0.1.0")
        assert ver == "2.0.0"


def test_read_ods_version_fallback_on_corrupt(tmp_path: Path):
    version_file = tmp_path / ".version"
    version_file.write_bytes(b"\x80\xff\xfeinvalid")
    with patch("routers.node._install_root", return_value=tmp_path):
        ver = _read_ods_version("0.1.0")
        assert ver == "0.1.0"
